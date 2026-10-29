"""Inspect a copy of the native RAG snapshot, compare exact vector rankings and generate genuinely cited answers."""
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
import argparse
import ipaddress
import json
import shutil
from rag.answer import generate_answer
from rag.embeddings import TextEncoder
from rag.store import create_store
from rag.retrieval import retrieve_consistent


def probe(source, state, output, base_url, model):
    """Preserve original database bytes while checking native live-row counts, exact cosine rankings and actual LLM replies."""
    import pandas as pd
    from pymilvus import MilvusClient
    parts = urlsplit(base_url)
    address = ipaddress.ip_address(parts.hostname)
    if parts.scheme != 'http' or not (address.is_loopback or address in ipaddress.ip_network('10.53.0.0/16')):
        raise ValueError('Use only the existing private llm-d Service or loopback')
    source = Path(source); state = Path(state); output = Path(output); output.mkdir(parents=True, exist_ok=False)
    config = json.loads((source / 'infra/rag/embedding.lock.json').read_bytes())
    names = ['knowledge.db', 'registry.db', 'chunks.parquet', 'chunks.json', 'vectors.json', 'documents.json']
    before = {name: sha256((state / name).read_bytes()).hexdigest() for name in names}
    copied = output / 'snapshot-copy'; copied.mkdir()
    for name in names: shutil.copyfile(state / name, copied / name)
    frame = pd.read_parquet(copied / 'chunks.parquet'); chunks = json.loads((copied / 'chunks.json').read_bytes())
    vectors = json.loads((copied / 'vectors.json').read_bytes())['vectors']
    if len(frame) != len(chunks) or len(chunks) != len(vectors) or len(chunks) != 18: raise ValueError('The bounded snapshot changed')
    native = MilvusClient(str(copied / 'knowledge.db')); collections = native.list_collections()
    if len(collections) != 1: raise ValueError('The snapshot must contain one isolated feature collection')
    counts = native.query(collection_name=collections[0], filter='', output_fields=['count(*)'])
    if counts != [{'count(*)': 18}]: raise ValueError('Native post-replay live entity count differs from the 18 source chunks')
    encoder = TextEncoder(config); store, _, _ = create_store(copied, config)
    cases = [('Does high vibration by itself prove a pump failure?', 'monitoring'),
             ('Why does the API return HTTP 409 for prediction on a stopped pump?', 'prediction'),
             ('Does input drift alone prove that prediction accuracy has decreased?', 'drift')]
    results = []
    for question, document in cases:
        query = encoder.encode([question])[0]; hits, retrieval_trace = retrieve_consistent(store, query, top_k=3)
        scores = [sum(a * b for a, b in zip(query, vector)) for vector in vectors]
        top = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:3]
        expected = [chunks[i]['chunk_id'] for i in top]
        if hits['chunk_id'] != expected: raise ValueError('Native Feast/Milvus result differs from independent exact cosine ranking')
        records = {row['chunk_id']: row for row in chunks}
        for index, identifier in enumerate(hits['chunk_id']):
            if any(hits[name][index] != records[identifier][name] for name in ['text', 'document_id', 'revision', 'source_sha256', 'source_file', 'section']):
                raise ValueError('Retrieved text/provenance does not belong to its exact source chunk identity')
        answer = generate_answer(base_url, question, hits, output / ('answer-' + str(len(results)) + '.json'), model)
        if document not in [answer['citations'][key]['document_id'] for key in answer['used_citations']]:
            raise ValueError('The answer did not cite the question-specific source document')
        results.append({'question': question, 'expected_document': document, 'query_vector': query, 'all_cosine_scores': scores,
                        'expected_top_ids': expected, 'retrieved': hits, 'retrieval_trace': retrieval_trace, 'answer': answer})
    after = {name: sha256((state / name).read_bytes()).hexdigest() for name in names}
    if before != after: raise ValueError('The retained source snapshot changed during the copy-based probe')
    (output / 'responses.json').write_text(json.dumps(results, indent=2)+'\n')
    for name in ['chunks.json', 'vectors.json']: shutil.copyfile(state / name, output / name)
    report = {'passed': True, 'live_entities_after_two_sdk_writes': counts[0]['count(*)'], 'source_rows': len(frame),
              'exact_cosine_queries': len(results), 'actual_cited_llm_responses': len(results),
              'all_retrieved_passages_match_source_chunk_ids': True,
              'original_state_sha256_before': before, 'original_state_sha256_after': after,
              'scope': 'Bounded corpus, copied native snapshot, exact vector ranking and actual model replies. No Airflow/DataHub, MCP/agents or general QA-accuracy claim.'}
    (output / 'verification.json').write_text(json.dumps(report, indent=2)+'\n'); return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True); parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True); parser.add_argument('--base-url', required=True); parser.add_argument('--model', required=True)
    args = parser.parse_args(); print(json.dumps(probe(args.source, args.state, args.output, args.base_url, args.model), indent=2))
