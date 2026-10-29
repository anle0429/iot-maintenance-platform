"""Validate persisted RAG artifacts and capture actual schemas/readbacks for downstream governance publication."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import json
import math
from rag.pipeline import run_stage, write_new
from rag.store import create_store, FEATURES


def value_type(value):
    """Describe decoded JSON/SDK values without claiming an inferred Python number is a native SQL type."""
    if isinstance(value, bool): return 'boolean'
    if isinstance(value, int): return 'integer'
    if isinstance(value, float): return 'number'
    if isinstance(value, list): return 'array'
    return 'string'


def fields(rows):
    """Describe the keys and decoded value types actually present in a nonempty logical row dataset."""
    return [{'name': key, 'type': value_type(value), 'nullable': any(row.get(key) is None for row in rows)} for key, value in rows[0].items()]


def validate(source, directory):
    """Run native retrieval, verify all persisted rows and expose source-bound checks and real schemas."""
    import pandas as pd
    import pyarrow.parquet as pq
    from pymilvus import MilvusClient
    source = Path(source); directory = Path(directory)
    native = run_stage('validate', source, directory)
    write_new(directory / 'validate-report.json', dict(native, checked_utc=datetime.now(timezone.utc).isoformat()))
    documents = json.loads((directory / 'documents.json').read_bytes())['documents']
    chunks = json.loads((directory / 'chunks.json').read_bytes()); encoded = json.loads((directory / 'vectors.json').read_bytes())
    frame = pd.read_parquet(directory / 'chunks.parquet'); vectors = encoded['vectors']
    store, _, _ = create_store(directory, encoded['embedding_config'])
    online = store.get_online_features(features=['knowledge_chunks:' + name for name in FEATURES + ['embedding']],
                                      entity_rows=[{'chunk_id': row['chunk_id']} for row in chunks]).to_dict()
    client = MilvusClient(str(directory / 'knowledge.db')); collections = client.list_collections()
    if len(collections) != 1: raise ValueError('One isolated knowledge collection is required')
    count = client.query(collection_name=collections[0], filter='', output_fields=['count(*)'])[0]['count(*)']
    replay = json.loads((directory / 'store-readback.json').read_bytes())
    checks = {
        'source_hashes': all(sha256(row['text'].encode()).hexdigest() == row['sha256'] for row in documents),
        'unique_chunk_keys': len(set(row['chunk_id'] for row in chunks)) == len(chunks),
        'equal_stage_row_counts': len(chunks) == len(vectors) == len(frame) == count,
        'finite_unit_vectors': all(len(vector) == 384 and all(math.isfinite(v) for v in vector) and abs(sum(v*v for v in vector)-1) < .001 for vector in vectors),
        'online_text_matches_source': online['text'] == [row['text'] for row in chunks],
        'online_provenance_matches_source': all(online[name] == [row[name] for row in chunks] for name in ['document_id', 'revision', 'source_sha256', 'section']),
        'replay_readback_equal': replay['first'] == replay['replay'],
        'parquet_seal_matches': replay['parquet_sha256'] == sha256((directory / 'chunks.parquet').read_bytes()).hexdigest(),
        'native_semantic_queries': native['native_semantic_queries'] == 3 and native['expected_document_in_top_3'],
    }
    offline_fields = [{'name': field.name, 'type': str(field.type), 'nullable': field.nullable} for field in pq.read_schema(directory / 'chunks.parquet')]
    online_rows = [{key: values[i] for key, values in online.items()} for i in range(len(chunks))]
    descriptors = {
        'documents': {'rows': len(documents), 'fields': fields(documents), 'upstream': []},
        'chunks': {'rows': len(chunks), 'fields': fields(chunks), 'upstream': ['documents']},
        'embeddings': {'rows': len(vectors), 'fields': fields([{'chunk_id': chunks[0]['chunk_id'], 'embedding': vectors[0]}]), 'upstream': ['chunks']},
        'offline': {'rows': len(frame), 'fields': offline_fields, 'upstream': ['chunks', 'embeddings']},
        'online': {'rows': count, 'fields': fields(online_rows), 'upstream': ['offline']},
    }
    bound = ['documents.json', 'chunks.json', 'vectors.json', 'chunks.parquet', 'store-readback.json', 'retrieval.json', 'validate-report.json']
    report = {'passed': all(checks.values()), 'checked_utc': datetime.now(timezone.utc).isoformat(), 'checks': checks, 'datasets': descriptors,
              'native_live_entity_count': count, 'online_readback': online, 'embedding_config': encoded['embedding_config'],
              'artifact_sha256': {name: sha256((directory / name).read_bytes()).hexdigest() for name in bound}}
    write_new(directory / 'quality.json', report)
    if not report['passed']: raise ValueError('Persisted RAG validation failed; metadata publication is blocked')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--source', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); result = validate(args.source, args.output)
    print(json.dumps({'passed': result['passed'], 'checks': len(result['checks']), 'rows': result['native_live_entity_count']}))
