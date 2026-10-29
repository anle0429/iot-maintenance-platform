"""Read trusted native backends asynchronously and keep label-free cohort comparisons reproducible."""
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
import asyncio
import json
import threading
import httpx2
from pydantic import ValidationError
from services.mcp_tools.contracts import MachineData, PredictionData, DriftAssessment
from services.drift_api.contracts import CompareRequest


class ToolFailure(Exception):
    """Carry a public failure category without exposing upstream bodies, credentials or local file paths."""
    def __init__(self, status, message):
        """Retain the transport status and safe caller-facing explanation."""
        super().__init__(message)
        self.status = status


class KnowledgeReader:
    """Use a per-replica copy of the source-bound Feast/Milvus snapshot and the pinned CPU encoder."""
    def __init__(self, state, source_root):
        """Load the actual isolated registry and preserve a lookup from each chunk ID to its source record."""
        from rag.embeddings import TextEncoder
        from rag.store import create_store
        self.state = Path(state); source_root = Path(source_root)
        self.config = json.loads((source_root/'infra/rag/embedding.lock.json').read_bytes())
        rows = json.loads((self.state/'chunks.json').read_bytes())
        self.chunks = {row['chunk_id']: row for row in rows}
        if not rows or len(rows) != len(self.chunks): raise ValueError('Knowledge identities differ')
        catalog = json.loads((source_root/'knowledge/catalog.json').read_bytes())
        documents = {row['document_id']: row for row in catalog['documents']}
        if any(row['source_sha256'] != documents[row['document_id']]['sha256'] for row in rows):
            raise ValueError('The snapshot does not match the image source catalog')
        self.snapshot = sha256((self.state/'chunks.json').read_bytes()).hexdigest()
        self.store, _, _ = create_store(self.state, self.config)
        self.encoder = TextEncoder(self.config)
        self.lock = threading.Lock()

    def search(self, request):
        """Resolve native ranked IDs by singleton reads, then verify the exact text/provenance before returning it."""
        from rag.retrieval import retrieve_consistent
        with self.lock:
            try: vector = self.encoder.encode([request.question])[0]
            except ValueError: raise ToolFailure(422, 'Question exceeds the embedding model token limit') from None
            values, _ = retrieve_consistent(self.store, vector, request.top_k)
            rows = []
            for index, identifier in enumerate(values['chunk_id']):
                expected = self.chunks.get(identifier)
                fields = ['text','document_id','revision','source_sha256','source_file','section']
                if expected is None or any(values[name][index] != expected[name] for name in fields):
                    raise ToolFailure(503, 'Knowledge provenance check failed')
                rows.append(dict(expected, cosine_similarity=values['distance'][index]))
            return {'matches': rows, 'snapshot_sha256': self.snapshot,
                    'embedding_revision': self.config['revision'], 'source_type': 'authored-synthetic-reference'}


class ToolRuntime:
    """Adapt existing APIs without exposing arbitrary network requests or rematerialization tools."""
    FEATURE_URL = 'http://iot-feature-api.iot-features.svc.cluster.local'
    DRIFT_URL = 'http://iot-drift-api.iot-features.svc.cluster.local'

    def __init__(self, role, reference_id, knowledge=None, client=None):
        """Bind role, pinned reference and optional injected native clients/readers."""
        if role not in ['retrieval','drift']: raise ValueError('Unknown tool role')
        self.role = role; self.reference_id = reference_id; self.knowledge = knowledge
        self.client = client or httpx2.AsyncClient(timeout=8, follow_redirects=False, trust_env=False)
        self.slots = asyncio.Semaphore(8)

    async def start(self):
        """Load the read-only knowledge snapshot copy off the event loop before advertising readiness."""
        if self.role == 'retrieval' and self.knowledge is None:
            self.knowledge = await asyncio.to_thread(KnowledgeReader, '/state', '/workspace')

    async def close(self):
        """Release the shared upstream connection pool when the application shuts down."""
        await self.client.aclose()

    async def call(self, base, path, body=None):
        """Bound each request and translate failures without returning an upstream error body."""
        try:
            async with self.slots:
                response = await self.client.request('POST' if body is not None else 'GET', base+path, json=body)
            if response.status_code != 200:
                code = response.status_code if response.status_code in [404,409,422,507] else 503
                raise ToolFailure(code, 'Native upstream rejected or could not serve this request')
            if len(response.content) > 1_048_576: raise ToolFailure(503, 'Native response exceeds its contract')
            result = response.json()
            if not isinstance(result, dict): raise ValueError('Expected a JSON object')
            return result
        except ToolFailure: raise
        except (httpx2.HTTPError, ValueError): raise ToolFailure(503, 'Native upstream is unavailable or invalid') from None

    async def features(self, request):
        """Read one validated, identity-matching machine snapshot from the real Feature API."""
        raw = await self.call(self.FEATURE_URL, '/v1/machines/'+request.machine_id+'/features')
        try:
            record = MachineData.model_validate(raw)
            if record.machine_id != request.machine_id: raise ValueError('Different machine')
            return record.model_dump(mode='json')
        except (ValidationError, ValueError, KeyError): raise ToolFailure(503, 'Native feature identity or provenance differs') from None

    async def predict(self, request):
        """Preserve actual stopped/unknown/unavailable responses from the pinned native prediction path."""
        result = await self.call(self.FEATURE_URL, '/v1/machines/'+request.machine_id+':predict', {})
        try:
            record = PredictionData.model_validate(result)
            if record.feature_trace.machine_id != request.machine_id: raise ValueError('Different machine')
        except (ValidationError,ValueError,KeyError): raise ToolFailure(503, 'Native prediction identity or decision differs') from None
        return record.model_dump(mode='json')

    async def search(self, request):
        """Keep synchronous encoder/SDK work off the event loop and bound concurrent knowledge reads."""
        if self.knowledge is None: raise ToolFailure(503, 'Knowledge store is not ready')
        try:
            async with self.slots: return await asyncio.to_thread(self.knowledge.search, request)
        except ToolFailure: raise
        except Exception: raise ToolFailure(503, 'Knowledge store is unavailable or invalid') from None

    async def drift(self, request):
        """Compare a source-consistent cohort using fixed simulated times, so replay keeps the same window payload."""
        from services.mcp_tools.contracts import MachineRequest
        records = await asyncio.gather(*(self.features(MachineRequest(machine_id=identifier)) for identifier in sorted(request.machine_ids)))
        first = records[0]
        for field in ['as_of','prepared_wall_utc','protocol_sha256','source_manifest_sha256']:
            if any(row[field] != first[field] for row in records): raise ToolFailure(409, 'Cohort spans different source snapshots')
        typed = [MachineData.model_validate(row) for row in records]
        observation_end = typed[0].as_of + timedelta(microseconds=1)
        window = CompareRequest(reference_id=self.reference_id, window_id=request.window_id,
            start=min(row.feature_event_ts for row in typed), end=observation_end,
            observations=[{'id': row.machine_id, 'event_timestamp': row.feature_event_ts, 'available_at': row.as_of, 'features': row.features} for row in typed])
        raw = await self.call(self.DRIFT_URL, '/v1/drift:compare', window.model_dump(mode='json'))
        try: DriftAssessment.model_validate(raw)
        except (ValidationError,ValueError): raise ToolFailure(503, 'Native drift response is invalid') from None
        if raw.get('reference_id') != self.reference_id or raw.get('window_id') != request.window_id or raw.get('unique_rows') != len(records):
            raise ToolFailure(503, 'Native drift identity differs')
        return {'reference_id': raw['reference_id'], 'window_id': raw['window_id'], 'unique_rows': raw['unique_rows'],
                'drift_detected': raw['drift_detected'], 'drifted_feature_fraction': raw['drifted_feature_fraction'],
                'features': {key: {name: value[name] for name in ['psi','drifted']} for key,value in raw['features'].items()},
                'lineage': raw['lineage'], 'policy': raw['policy'], 'as_of': first['as_of'],
                'source_manifest_sha256': first['source_manifest_sha256'],
                'assessment_sha256': sha256(json.dumps(raw,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),
                'scope': raw['scope']}

    async def ready(self):
        """Check actual upstream readiness, including the drift reference identity used by this release."""
        await self.call(self.FEATURE_URL, '/health/ready')
        if self.role == 'drift':
            value = await self.call(self.DRIFT_URL, '/health/ready')
            if value.get('reference_id') != self.reference_id: raise ToolFailure(503, 'Drift reference differs from this release')
        elif self.knowledge is None: raise ToolFailure(503, 'Knowledge store is not ready')
        return {'ready': True, 'role': self.role}
