"""Read trusted native backends asynchronously and keep label-free cohort comparisons reproducible."""
from datetime import timedelta
from hashlib import sha256
import asyncio
import json
import os
import httpx2
from pydantic import ValidationError
from services.mcp_tools.contracts import MachineData, PredictionData, DriftAssessment
from services.drift_api import reference_matches_model
from services.drift_api.contracts import CompareRequest
from services.rag_search.contracts import SearchResult, read_service_token


class ToolFailure(Exception):
    """Carry a public failure category without exposing upstream bodies, credentials or local file paths."""
    def __init__(self, status, message):
        """Retain the transport status and safe caller-facing explanation."""
        super().__init__(message)
        self.status = status


class ToolRuntime:
    """Adapt existing APIs without exposing arbitrary network requests or rematerialization tools."""
    FEATURE_URL = 'http://iot-feature-api.iot-features.svc.cluster.local'
    DRIFT_URL = 'http://iot-drift-api.iot-features.svc.cluster.local'

    DEFAULT_RAG_URL = 'http://iot-retrieval-mcp-search.iot-agents.svc.cluster.local'

    def __init__(self, role, reference_id, client=None, *, rag_token=None, drift_scope='fleet_reference'):
        """Bind role, pinned reference and the client for fixed private HTTP backends."""
        if role not in ['retrieval','drift']: raise ValueError('Unknown tool role')
        if drift_scope not in ('serving_model', 'fleet_reference'):
            raise ValueError('Unknown drift scope')
        self.role = role
        self.reference_id = reference_id
        self.drift_scope = drift_scope
        self.rag_url = os.environ.get('IOT_RAG_SEARCH_URL', self.DEFAULT_RAG_URL).rstrip('/')
        self.rag_token = rag_token
        self.client = client or httpx2.AsyncClient(timeout=8, follow_redirects=False, trust_env=False)
        self.slots = asyncio.Semaphore(8)
        self.search_slots = asyncio.Semaphore(8)

    async def start(self):
        """Load only this role's search service credential before accepting calls."""
        if self.role == 'retrieval' and self.rag_token is None:
            self.rag_token = read_service_token()

    async def close(self):
        """Release the shared upstream connection pool when the application shuts down."""
        await self.client.aclose()

    async def call(self, base, path, body=None, *, headers=None, slots=None):
        """Bound each request and translate failures without returning an upstream error body."""
        try:
            async with self.slots if slots is None else slots:
                async with self.client.stream('POST' if body is not None else 'GET', base+path,
                                              json=body, headers=headers) as response:
                    if response.status_code == 429 and slots is self.search_slots:
                        raise ToolFailure(429, 'Knowledge search is busy')
                    if response.status_code != 200:
                        code = response.status_code if response.status_code in [404,409,422,507] else 503
                        raise ToolFailure(code, 'Native upstream rejected or could not serve this request')
                    received = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(received) + len(chunk) > 1_048_576:
                            raise ToolFailure(503, 'Native response exceeds its contract')
                        received.extend(chunk)
            result = json.loads(received)
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
        """Request ranked passages from the private search API and validate their public schema."""
        if not self.rag_token:
            raise ToolFailure(503, 'Knowledge service credential is unavailable')
        if self.search_slots.locked():
            raise ToolFailure(429, 'Knowledge tool is busy')
        result = await self.call(self.rag_url, '/v1/knowledge:search', request.model_dump(),
                                 headers={'Authorization': 'Bearer ' + self.rag_token}, slots=self.search_slots)
        try:
            validated = SearchResult.model_validate(result)
            if len(validated.matches) != request.top_k:
                raise ValueError('Wrong number of matches')
            return validated.model_dump(mode='json')
        except ValueError:
            raise ToolFailure(503, 'Knowledge service returned invalid results') from None

    async def drift(self, request):
        """Compare a source-consistent cohort using fixed simulated times, so replay keeps the same window payload."""
        from services.mcp_tools.contracts import MachineRequest
        ids = sorted(request.machine_ids)
        model = None
        if self.drift_scope == 'serving_model':
            inputs = await asyncio.gather(*(self.drift_input(identifier) for identifier in ids))
            model = inputs[0][1]
            if any(other != model for _, other in inputs):
                raise ToolFailure(409, 'Cohort spans different serving model releases')
            records = [row for row, _ in inputs]
        else:
            records = await asyncio.gather(*(self.features(MachineRequest(machine_id=identifier)) for identifier in ids))
        first = records[0]
        for field in ['as_of','prepared_wall_utc','protocol_sha256','source_manifest_sha256']:
            if any(row[field] != first[field] for row in records): raise ToolFailure(409, 'Cohort spans different source snapshots')
        if model is not None:
            records = [row for row in records if row['operating_context']['status'] == 'operating']
            if len(records) < 32:
                raise ToolFailure(422, 'Serving-model drift requires at least 32 operating machines')
            reference = await self.call(self.DRIFT_URL, '/v1/drift/reference')
            if (reference.get('reference_id') != self.reference_id
                    or not reference_matches_model(reference.get('lineage'), model)):
                raise ToolFailure(409, 'Drift reference differs from the serving model release')
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
        if model is not None and not reference_matches_model(raw['lineage'], model):
            raise ToolFailure(503, 'Native drift lineage differs from the serving model release')
        return {'reference_id': raw['reference_id'], 'window_id': raw['window_id'], 'unique_rows': raw['unique_rows'],
                'drift_detected': raw['drift_detected'], 'drifted_feature_fraction': raw['drifted_feature_fraction'],
                'features': {key: {name: value[name] for name in ['psi','drifted']} for key,value in raw['features'].items()},
                'lineage': raw['lineage'], 'policy': raw['policy'], 'as_of': first['as_of'],
                'source_manifest_sha256': first['source_manifest_sha256'],
                'assessment_sha256': sha256(json.dumps(raw,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),
                'scope': raw['scope']}

    async def drift_input(self, identifier):
        """Keep machine eligibility and the model pin together across a rolling release."""
        raw = await self.call(self.FEATURE_URL, '/v1/machines/'+identifier+'/drift-input')
        try:
            record = MachineData.model_validate(raw['feature'])
            model = raw['serving_model']
            if (record.machine_id != identifier or not isinstance(model, dict) or model.get('production') is not True
                    or model.get('protocol_sha256') != record.protocol_sha256):
                raise ValueError('Mismatched identity')
        except (KeyError, TypeError, ValueError):
            raise ToolFailure(503, 'Serving drift input has invalid machine or model provenance') from None
        return record.model_dump(mode='json'), model

    async def ready(self):
        """Check the native dependencies for release verification and drift service readiness."""
        feature = await self.call(self.FEATURE_URL, '/health/ready')
        if feature.get('ready') is not True:
            raise ToolFailure(503, 'Feature service is not ready')
        if self.role == 'drift':
            value = await self.call(self.DRIFT_URL, '/health/ready')
            if value.get('reference_id') != self.reference_id: raise ToolFailure(503, 'Drift reference differs from this release')
        else:
            value = await self.call(self.rag_url, '/health/ready', slots=self.search_slots)
            if value.get('ready') is not True or value.get('service') != 'rag-search':
                raise ToolFailure(503, 'Knowledge service is not ready')
        return {'ready': True, 'role': self.role}
