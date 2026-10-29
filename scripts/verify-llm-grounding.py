"""Verify the real 7B model switch and bounded, source-cited Feast retrieval/generation receipts."""
from hashlib import sha256
import json
import math
import re
from integration_evidence import capsule, read, sources, exited

MANIFEST_SHA256 = 'ac604a73bc27c9375bcbda88d0c136656dba611f7526d75764e39b674c9e739e'


def verify():
    """Independently recompute rankings/provenance while preserving the rejected smaller-model response."""
    base, payloads = capsule('llm-grounding', MANIFEST_SHA256)
    sources(base, 'candidate', current=True)
    sources(base, 'grounded', current=True)
    sources(base, 'small-model-rejected')
    sources(base, 'misaligned-columns')
    lock = read(base, 'candidate/native/model-lock.json')
    download = read(base, 'candidate/native/download.json')
    assert download['files'] == lock['files'] and download['verified_actual_bytes'] == 4_683_073_632
    assert sum(row['bytes'] for row in lock['files']) == download['verified_actual_bytes']
    values = read(base, 'candidate/native/candidate-values.json')
    assert values['servedModel'] == lock['served_model'] == 'iot-qwen2.5-7b'
    assert values['resources']['limits'] == {'cpu': '4', 'memory': '10Gi'}
    assert read(base, 'candidate/native/response.json')['model'] == lock['served_model']
    resources = read(base, 'candidate/native/resources-after.stdout')['items']
    pods = [item for item in resources if item['kind'] == 'Pod']
    assert pods and all(all(status['ready'] for status in pod['status']['containerStatuses']) for pod in pods)
    assert read(base, 'candidate/native/verification.json')['passed']
    for prefix, code in [('grounded', 0), ('small-model-rejected', 1)]:
        container = read(base, prefix + '/observed-container.json')[0]
        exited(container, code)
        assert container['Config']['User'] == '10001:10001' and container['HostConfig']['ReadonlyRootfs']
    original = read(base, 'small-model-rejected/native/proof/result/answer-0.json')
    assert original['raw_response']['model'] == 'iot-qwen2.5-1.5b'
    assert 'Yes, high vibration alone does not prove' in original['raw_response']['choices'][0]['message']['content']
    assert 'did not cite the question-specific source document' in (base / 'small-model-rejected/native/native.stderr').read_text()
    prefix = 'grounded/native/proof/result/'
    report = read(base, prefix + 'verification.json')
    assert report['passed'] and report['original_state_sha256_before'] == report['original_state_sha256_after']
    assert report['all_retrieved_passages_match_source_chunk_ids']
    assert report['live_entities_after_two_sdk_writes'] == report['source_rows'] == 18
    chunks = read(base, prefix + 'chunks.json')
    vectors = read(base, prefix + 'vectors.json')['vectors']
    assert len(chunks) == len(vectors) == 18 and len({row['chunk_id'] for row in chunks}) == 18
    records = {row['chunk_id']: row for row in chunks}
    responses = read(base, prefix + 'responses.json')
    assert len(responses) == report['actual_cited_llm_responses'] == report['exact_cosine_queries'] == 3
    for index, (row, expected) in enumerate(zip(responses, ['monitoring', 'prediction', 'drift'])):
        query = row['query_vector']
        assert len(query) == 384 and all(math.isfinite(x) for x in query)
        assert abs(sum(x*x for x in query)-1) < .001
        scores = [sum(a*b for a, b in zip(query, vector)) for vector in vectors]
        assert scores == row['all_cosine_scores']
        top = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:3]
        assert row['expected_top_ids'] == row['retrieved']['chunk_id'] == [chunks[i]['chunk_id'] for i in top]
        assert row['retrieval_trace']['native_search']['chunk_id'] == row['expected_top_ids']
        assert len(row['retrieval_trace']['singleton_feature_reads']) == 3
        answer = row['answer']; raw = answer['raw_response']; text = raw['choices'][0]['message']['content']
        assert raw['model'] == lock['served_model'] and raw['usage']['completion_tokens'] > 0
        assert row['expected_document'] == expected
        used = sorted(set(re.findall(r'\[(D\d+)\]', text)))
        assert used == answer['used_citations'] and expected in [answer['citations'][key]['document_id'] for key in used]
        receipt = read(base, prefix + f'answer-{index}.json')
        for key in ['question', 'request', 'raw_response', 'citations']: assert receipt[key] == answer[key]
        for label, citation in answer['citations'].items():
            chunk = records[citation['chunk_id']]
            assert all(citation[key] == chunk[key] for key in ['document_id', 'revision', 'section', 'source_sha256'])
            source = base / 'grounded/source/tree/knowledge' / chunk['source_file']
            assert sha256(source.read_bytes()).hexdigest() == chunk['source_sha256'] and chunk['text'] in source.read_text()
    previous = read(base, 'misaligned-columns/native/proof/result/responses.json')
    assert any(row['retrieved']['text'][i] != records[key]['text'] for row in previous for i, key in enumerate(row['retrieved']['chunk_id']))
    return {'passed': True, 'payloads': payloads, 'model': lock['served_model'], 'live_entities_after_replay': 18,
            'independent_exact_rankings': 3, 'original_cited_responses': 3, 'failed_smaller_model_retained': True,
            'previous_cross_column_misalignment_retained': True,
            'scope': 'Three inspected reference questions on a small authored corpus. No general QA accuracy, 7B throughput, MCP or agent-runtime claim.'}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
