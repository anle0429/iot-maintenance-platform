"""Verify original native CPU inference, llm-d routing, source bindings and before/after measurements offline."""
from hashlib import sha256
from pathlib import Path
from statistics import median
import json
import math

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'docs/evidence/llm-inference'
MANIFEST_SHA256 = '6439b42c8bd21ee1206e98ab38930a0557b531d12c03cddcc860e7ae016febde'


def read(name):
    """Decode a captured response without rewriting its original bytes."""
    return json.loads((BASE / name).read_bytes())


def measurements(profile, repeats):
    """Recompute summaries from original client rows, actual stream usage and matching fixed-workload metadata."""
    prefix = 'native/llm-foundation-29b/' + profile
    data = read(prefix + '/requests.json'); summary = read(prefix + '/summary.json')
    assert len(data['workload']) == 3 and len(data['warmup']) == 3 and len(data['measured']) == 3 * repeats
    parameters = data['parameters']
    assert parameters['cache_prompt'] is False and parameters['temperature'] == 0 and parameters['seed'] == 7
    assert parameters['max_tokens'] == 64 and parameters['model'] == 'iot-qwen2.5-1.5b'
    for i, row in enumerate(data['measured']):
        assert (row['repeat'], row['prompt_index']) == (i // 3, i % 3)
        assert row['status'] == 200 and 0 < row['ttft_seconds'] <= row['elapsed_seconds']
        events = row['events']; usage = next(event['usage'] for event in reversed(events) if event.get('usage'))
        assert usage == row['usage'] and usage['prompt_tokens_details']['cached_tokens'] == 0
        assert 0 < usage['completion_tokens'] <= 64 and usage['total_tokens'] == usage['prompt_tokens'] + usage['completion_tokens']
        reconstructed = ''.join(choice.get('delta', {}).get('content', '') or '' for event in events for choice in event.get('choices', []))
        assert reconstructed == row['text'] and reconstructed
        assert math.isclose(row['completion_tokens_per_second'], usage['completion_tokens'] / row['elapsed_seconds'])
    assert summary['requests'] == 3 * repeats and summary['warmup_requests'] == 3
    for metric, field in [('median_ttft_seconds', 'ttft_seconds'), ('median_elapsed_seconds', 'elapsed_seconds'),
                          ('median_completion_tokens_per_second', 'completion_tokens_per_second')]:
        assert math.isclose(summary[metric], median(row[field] for row in data['measured']))
    assert summary['reported_completion_tokens'] == sum(row['usage']['completion_tokens'] for row in data['measured'])
    return data, summary


def verify():
    """Verify a bounded native inference milestone while leaving all other LLM/agent obligations open."""
    raw = (BASE / 'manifest.json').read_bytes(); assert sha256(raw).hexdigest() == MANIFEST_SHA256
    manifest = json.loads(raw)
    for name, metadata in manifest['files'].items():
        path = (BASE / name).resolve(); assert path.is_relative_to(BASE.resolve())
        payload = path.read_bytes(); assert len(payload) == metadata['bytes'] and sha256(payload).hexdigest() == metadata['sha256'], name
    for label, run in [('initial', 'llm-foundation-29a'), ('completion', 'llm-foundation-29b')]:
        seal = read('native/' + run + '/source.json')
        for name, digest in seal['files'].items():
            assert sha256((BASE / 'source-snapshots' / label / name).read_bytes()).hexdigest() == digest
            if label == 'completion': assert sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    failure = read('native/llm-foundation-29a/failure.json'); assert failure['type'] == 'URLError' and 'No route to host' in failure['message']
    baseline, before = measurements('baseline', 3); optimized, after = measurements('optimized', 3); routed, routed_summary = measurements('routed', 1)
    assert baseline['workload'] == optimized['workload'] == routed['workload'] and baseline['parameters'] == optimized['parameters'] == routed['parameters']
    assert [row['text'] for row in baseline['measured']] == [row['text'] for row in optimized['measured']]
    assert (BASE / 'native/llm-foundation-29a/baseline/requests.json').read_bytes() == (BASE / 'native/llm-foundation-29b/baseline/requests.json').read_bytes()
    report = read('native/llm-foundation-29b/verification.json'); lock = read('native/llm-foundation-29b/model-lock.json')
    assert report['passed'] and report['model_sha256'] == lock['sha256'] and report['model_file_bytes'] == lock['bytes'] == 1117320736
    download = read('native/llm-foundation-29a/download.json'); assert download['sha256'] == lock['sha256'] and download['bytes'] == lock['bytes']
    model_values = read('native/llm-foundation-29b/helm-model.stdout'); router_values = read('native/llm-foundation-29b/helm-router.stdout')['router']
    assert model_values['image'] == lock['server_image'] and model_values['threads'] == 4 and model_values['contextTokens'] == 4096
    assert router_values['epp']['image']['tag'] == 'v1.0.0' and router_values['proxy']['failOpen'] is False
    assert 'inflight-load-producer' in router_values['epp']['pluginsCustomConfig']['iot-load.yaml']
    resources = read('native/llm-foundation-29b/resources-after.stdout')['items']
    deployments = [r for r in resources if r['kind'] == 'Deployment']; services = [r for r in resources if r['kind'] == 'Service']
    assert len(deployments) == len(services) == 2 and all(r['status']['availableReplicas'] == 1 for r in deployments)
    assert all(r['spec']['type'] == 'ClusterIP' for r in services)
    pods = [r for r in resources if r['kind'] == 'Pod']; assert len(pods) == 2
    assert all(c['ready'] and c['restartCount'] == 0 for r in pods for c in r['status']['containerStatuses'])
    assert any(c['imageID'] == lock['server_image'] for r in pods for c in r['status']['containerStatuses'])
    pools = [r for r in resources if r['kind'] == 'InferencePool']; assert len(pools) == 1
    tool = read('native/llm-foundation-29b/tool-response.json'); call = tool['choices'][0]['message']['tool_calls'][0]['function']
    assert call['name'] == 'get_machine_features' and json.loads(call['arguments']) == {'machine_id': 'P-001'}
    assert tool['choices'][0]['finish_reason'] == 'tool_calls'
    logs = (BASE / 'native/llm-foundation-29b/router-logs.stdout').read_text()
    assert 'EPP received request' in logs and 'EPP sent response body back to proxy' in logs
    return {'passed': True, 'payloads': len(manifest['files']), 'direct_measured_requests': 18, 'routed_measured_requests': 3,
            'before_median_seconds': before['median_elapsed_seconds'], 'after_median_seconds': after['median_elapsed_seconds'],
            'observed_latency_ratio': before['median_elapsed_seconds'] / after['median_elapsed_seconds'],
            'before_ttft_seconds': before['median_ttft_seconds'], 'after_ttft_seconds': after['median_ttft_seconds'],
            'routed_median_seconds': routed_summary['median_elapsed_seconds'],
            'scope': 'Historical native single-model CPU inference/routing and source-bound measurements. Tool selection is not MCP execution; RAG/agents/global config/HA remain separate obligations.'}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
