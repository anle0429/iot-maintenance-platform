"""Check the original source-bound native Feast/Milvus RAG-core run without loading models or starting containers."""
from hashlib import sha256
from pathlib import Path
import json
import math

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'docs/evidence/rag-foundation'
MANIFEST_SHA256 = '18d4b5c8b5e3e49193827b6329cfeb88d5e995bcb7dfbc723cc80b873666666c'


def read(name):
    """Decode an original captured JSON response without changing it."""
    return json.loads((BASE / name).read_bytes())


def verify():
    """Validate captured source, real exit/resource state, provenance, replay values and bounded retrieval results."""
    raw = (BASE / 'manifest.json').read_bytes(); assert sha256(raw).hexdigest() == MANIFEST_SHA256
    manifest = json.loads(raw)
    for name, row in manifest['files'].items():
        path = (BASE / name).resolve(); assert path.is_relative_to(BASE.resolve())
        payload = path.read_bytes(); assert len(payload) == row['bytes'] and sha256(payload).hexdigest() == row['sha256'], name
    seal = read('native/source.json')
    for name, digest in seal['files'].items():
        assert sha256((BASE / 'source-snapshot' / name).read_bytes()).hexdigest() == digest
        assert sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    report = read('native/verification.json'); assert report['passed']
    for stage in ['fetch', 'chunk', 'embed', 'store', 'validate']:
        container = read('native/' + stage + '-container.stdout')[0]
        assert container['Image'] == report['image_id'] and not container['State']['Running']
        assert container['State']['ExitCode'] == 0 and not container['State']['OOMKilled']
        assert container['Config']['User'] == '10001:10001' and container['HostConfig']['ReadonlyRootfs']
        assert container['HostConfig']['NanoCpus'] == 2_000_000_000 and container['HostConfig']['Memory'] == container['HostConfig']['MemorySwap'] == 4 * 1024**3
    assert read('native/fetch-report.json')['documents'] == 4
    assert read('native/chunk-report.json')['chunks'] == 18
    assert read('native/embed-report.json')['vectors'] == 18 and read('native/embed-report.json')['dimensions'] == 384
    rows = read('native/store-readback.json'); assert rows['first'] == rows['replay'] and rows['rows'] == 18
    assert len(rows['first']['text']) == len(set(rows['first']['chunk_id'])) == 18
    catalog = read('source-snapshot/knowledge/catalog.json')
    docs = {row['document_id']: row for row in catalog['documents']}
    results = read('native/retrieval.json'); assert len(results) == 3
    expected_documents = ['monitoring', 'prediction', 'drift']
    for result, expected in zip(results, expected_documents):
        values = result['result']; assert result['expected_document'] == expected and expected in values['document_id']
        assert len(values['document_id']) == 3 and len(set(values['chunk_id'])) == 3
        for i, document_id in enumerate(values['document_id']):
            document = docs[document_id]
            source = BASE / 'source-snapshot/knowledge' / document['file']
            assert values['source_sha256'][i] == document['sha256'] == sha256(source.read_bytes()).hexdigest()
            assert values['revision'][i] == document['revision'] == 1 and values['text'][i] in source.read_text(encoding='utf-8')
            vector = values['embedding'][i]; assert len(vector) == 384 and all(math.isfinite(v) for v in vector)
            assert abs(sum(v * v for v in vector) - 1) < .001
    assert (BASE / 'native/pip-check.stdout').read_text().strip() == 'No broken requirements found.'
    return {'passed': True, 'payloads': len(manifest['files']), 'documents': 4, 'chunks': 18, 'embedding_dimensions': 384,
            'native_query_cases': 3, 'replay_values_identical': True,
            'scope': 'Historical bounded RAG core: real encoder, native Feast/Milvus and expected documents in top three. Not Airflow, DataHub, full QA accuracy, MCP/agents or a database-transaction guarantee.'}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
