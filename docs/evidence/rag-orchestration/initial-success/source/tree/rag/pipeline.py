"""Run independently observable fetch, chunk, embedding, native Feast storage and retrieval-validation stages."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import json
from rag.corpus import read_catalog, chunk_documents


def write_new(path, value):
    """Preserve stage outputs so retries cannot overwrite a prior successful or failed run silently."""
    with Path(path).open('x', encoding='utf-8') as stream: json.dump(value, stream, indent=2); stream.write('\n')


def run_stage(stage, source, output):
    """Execute one pipeline boundary using only predecessor artifacts from the selected immutable run."""
    source = Path(source).resolve(); output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    config = json.loads((source / 'infra/rag/embedding.lock.json').read_bytes())
    if stage == 'fetch':
        documents = read_catalog(source / 'knowledge')
        write_new(output / 'documents.json', {'captured_utc': datetime.now(timezone.utc).isoformat(), 'documents': documents})
        return {'documents': len(documents)}
    if stage == 'chunk':
        documents = json.loads((output / 'documents.json').read_bytes())['documents']
        chunks = chunk_documents(documents, config['chunk_max_chars'], config['overlap_chars'])
        write_new(output / 'chunks.json', chunks)
        return {'chunks': len(chunks), 'source_documents': len(documents)}
    if stage == 'embed':
        from rag.embeddings import TextEncoder
        chunks = json.loads((output / 'chunks.json').read_bytes())
        encoder = TextEncoder(config); vectors = encoder.encode([chunk['text'] for chunk in chunks])
        write_new(output / 'vectors.json', {'embedding_config': config, 'vectors': vectors})
        return {'vectors': len(vectors), 'dimensions': config['dimensions'], 'model_revision': config['revision']}
    if stage == 'store':
        import pandas as pd
        from rag.store import create_store
        if (output / 'registry.db').exists() or (output / 'knowledge.db').exists(): raise ValueError('Preserve the existing store; choose a new run')
        chunks = json.loads((output / 'chunks.json').read_bytes()); vectors = json.loads((output / 'vectors.json').read_bytes())
        if vectors['embedding_config'] != config or len(vectors['vectors']) != len(chunks): raise ValueError('Embedding/source configuration mismatch')
        stamp = json.loads((output / 'documents.json').read_bytes())['captured_utc']
        rows = [dict(chunk, embedding=vector, embedding_revision=config['revision'], event_timestamp=pd.Timestamp(stamp), created=pd.Timestamp(stamp)) for chunk, vector in zip(chunks, vectors['vectors'])]
        frame = pd.DataFrame(rows); frame.to_parquet(output / 'chunks.parquet', index=False)
        store, entity, view = create_store(output, config); store.apply([entity, view])
        store.write_to_online_store(feature_view_name='knowledge_chunks', df=frame)
        first = store.get_online_features(features=['knowledge_chunks:text'], entity_rows=[{'chunk_id': row['chunk_id']} for row in rows]).to_dict()
        if first['text'] != [row['text'] for row in rows]: raise ValueError('Native Feast key readback differs from source')
        store.write_to_online_store(feature_view_name='knowledge_chunks', df=frame)
        replay = store.get_online_features(features=['knowledge_chunks:text'], entity_rows=[{'chunk_id': row['chunk_id']} for row in rows]).to_dict()
        if replay != first: raise ValueError('Native replay changed the returned knowledge values')
        write_new(output / 'store-readback.json', {'first': first, 'replay': replay, 'rows': len(rows), 'parquet_sha256': sha256((output / 'chunks.parquet').read_bytes()).hexdigest()})
        return {'native_feast_rows': len(rows), 'identical_replay_readback': True}
    if stage == 'validate':
        from rag.embeddings import TextEncoder
        from rag.store import create_store, retrieve
        encoder = TextEncoder(config); store, _, _ = create_store(output, config)
        cases = [('What does a high vibration reading mean?', 'monitoring'), ('Why is prediction rejected for a stopped pump?', 'prediction'),
                 ('Does sensor distribution drift prove lower model accuracy?', 'drift')]
        responses = []
        for question, expected_document in cases:
            actual = retrieve(store, encoder.encode([question])[0], top_k=3)
            if expected_document not in actual['document_id']: raise ValueError('Expected reference missing from native retrieval: ' + question)
            responses.append({'question': question, 'expected_document': expected_document, 'result': actual})
        write_new(output / 'retrieval.json', responses)
        return {'native_semantic_queries': len(responses), 'expected_document_in_top_3': True}
    raise ValueError('Unknown stage')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['fetch', 'chunk', 'embed', 'store', 'validate']); parser.add_argument('--source', type=Path, default=Path('.'))
    parser.add_argument('--output', type=Path, required=True); args = parser.parse_args()
    result = run_stage(args.stage, args.source, args.output)
    write_new(args.output / (args.stage + '-report.json'), dict(result, checked_utc=datetime.now(timezone.utc).isoformat()))
    print(json.dumps(result))
