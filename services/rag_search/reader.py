"""Serve ranked, provenance-checked passages from one sealed knowledge snapshot."""
from hashlib import sha256
from pathlib import Path
import json
import threading


class KnowledgeFailure(Exception):
    """Expose only a bounded HTTP status and caller-safe failure message."""

    def __init__(self, status, message):
        """Keep the failure category independent of the MCP transport."""
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
            except ValueError: raise KnowledgeFailure(422, 'Question exceeds the embedding model token limit') from None
            values, _ = retrieve_consistent(self.store, vector, request.top_k)
            rows = []
            for index, identifier in enumerate(values['chunk_id']):
                expected = self.chunks.get(identifier)
                fields = ['text','document_id','revision','source_sha256','source_file','section']
                if expected is None or any(values[name][index] != expected[name] for name in fields):
                    raise KnowledgeFailure(503, 'Knowledge provenance check failed')
                rows.append(dict(expected, cosine_similarity=values['distance'][index]))
            return {'matches': rows, 'snapshot_sha256': self.snapshot,
                    'embedding_revision': self.config['revision'], 'source_type': 'authored-synthetic-reference'}
