"""Recompute the paired native model comparison, original SSE usage and Grafana vectors without cloud access."""
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import json
import sys
import tarfile
from integration_evidence import ROOT, capsule, read
sys.path.insert(0, str(ROOT))
from llm.ab_experiment import MODELS, CASES, summarize, reply_text, active_model_pod, prometheus_metrics
from llm_observability.contracts import StreamObservation

MANIFEST = '36ac8a66135aab39a3d6c644215b5cb10654ce3fa4899d9083bca01fb901e9a4'


def verify():
    """Authenticate original attempts and independently reconcile every accepted request with its raw response and clock."""
    base, count = capsule('llm-model-ab', MANIFEST)
    assert count == 10
    meta = read(base, 'native-archive.json'); raw = (base / 'native-receipts.tar.gz').read_bytes()
    assert len(raw) == meta['archive_bytes'] == 255957
    assert sha256(raw).hexdigest() == meta['archive_sha256'] == '54bb5addc3f80a533533a01e0fd3b049a58b2b417d9cdaf9cc04d0270e44556d'
    assert meta['actual_native_credentials_scanned'] and meta['pc_actual_gpu_join_credential_scanned']
    assert not meta['recompressed_or_rewritten_on_pc'] and not meta['workloads_rerun_for_capture']
    bodies = {}
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            assert member.isdir() or member.isfile()
            if not member.isfile(): continue
            name = PurePosixPath(member.name).relative_to('llm-model-ab-capture-30a')
            assert not name.is_absolute() and '..' not in name.parts and member.size < 10000000
            name = name.as_posix(); assert name not in bodies
            bodies[name] = archive.extractfile(member).read()
    assert sha256(bodies['manifest.json']).hexdigest() == meta['original_inventory_sha256'] == '14a3beb8eea638850328c5fabcb8612acec927acdc5fd21ad4a3be679c41abbd'
    manifest = json.loads(bodies['manifest.json'])
    assert len(manifest['files']) == meta['native_payloads'] == 484
    assert set(bodies) == set(manifest['files']) | {'manifest.json'}
    for name, pin in manifest['files'].items():
        assert len(bodies[name]) == pin['bytes'] and sha256(bodies[name]).hexdigest() == pin['sha256'], name
    native = lambda name: json.loads(bodies[name])
    for folder in ['ab-models-22d630e9', 'ab-models-a617c658', 'model-study-b7be0dba', 'model-study-25010274', 'model-study-0c8d00c2', 'ab-view-92b667d3']:
        prefix = 'source/' + folder + '/'; seal = native(prefix + 'manifest.json')
        for name, pin in seal['files'].items():
            original = bodies[prefix + 'tree/' + name]
            assert sha256(original).hexdigest() == pin, name
            if folder in ['ab-models-a617c658', 'model-study-0c8d00c2', 'ab-view-92b667d3']:
                assert (ROOT / name).read_bytes().replace(b'\r\n', b'\n') == original, name
    prefix = 'native/llm-model-study-30c/'
    rows = native(prefix + 'requests.json'); assert native(prefix + 'cases.json') == CASES
    for row in rows:
        label = '-'.join(str(row[k]) for k in ['phase', 'repeat', 'case_index', 'variant'])
        wire = bodies[prefix + label + '.sse']; http = native(prefix + label + '-http.json')
        assert sha256(wire).hexdigest() == http['wire_sha256'] == row['wire_sha256']
        assert row['request_body'] == native(prefix + label + '-request.json')
        assert http['status'] == row['status'] == 200 and http['elapsed_seconds'] == row['elapsed_seconds']
        parser = StreamObservation(); offset = 0
        for chunk in http['chunks']:
            assert chunk['offset'] == offset and chunk['bytes'] > 0
            parser.feed(wire[offset:offset + chunk['bytes']], chunk['elapsed_seconds']); offset += chunk['bytes']
        assert offset == len(wire)
        observed = parser.finish(http['elapsed_seconds'])
        assert observed['complete'] and observed['usage'] == row['usage'] and observed['ttft_seconds'] == row['ttft_seconds']
        events = [json.loads(line[5:].strip()) for line in wire.decode().splitlines() if line.startswith('data:') and line[5:].strip() != '[DONE]']
        assert reply_text(events) == row['text']
        assert {v['model'] for v in events if 'model' in v} == {MODELS[row['variant']]}
        assert {v['system_fingerprint'] for v in events if 'system_fingerprint' in v} == {'b11977-7ac5727d6'}
        usage = next(v['usage'] for v in reversed(events) if v.get('usage'))
        assert usage['prompt_tokens_details']['cached_tokens'] == row['cached_tokens'] == 0
    summary = summarize(rows); report = native(prefix + 'verification.json')
    assert summary == native(prefix + 'summary.json') == report['summary'] == read(base, 'comparison.json')['summary']
    assert report['passed'] and report['measured_requests'] == 24 and report['warmup_requests'] == 8
    assert {v: summary[v]['correct_contract_answers'] for v in MODELS} == {'small': 3, 'large': 9}
    assert bodies[prefix + 'experiment.prom'] == prometheus_metrics(summary)
    for variant in MODELS:
        def predicted(when):
            lines = [s for s in bodies[prefix + 'metrics-' + when + '-' + variant + '.prom'].decode().splitlines() if s.startswith('llamacpp:tokens_predicted_total ')]
            assert len(lines) == 1
            return float(lines[0].split()[-1])
        delta = predicted('after') - predicted('before')
        assert delta == sum(r['usage']['completion_tokens'] for r in rows if r['variant'] == variant) == report['native_generated_token_counter_deltas'][variant]
        props = native(prefix + 'props-' + variant + '.json')
        assert props['model_alias'] == MODELS[variant] and props['total_slots'] == 1 and props['default_generation_settings']['n_ctx'] == 8192
    before = native(prefix + 'model-before.json'); after = native(prefix + 'model-after.json')
    a = active_model_pod(native(prefix + 'pods-before.json'), before)
    b = active_model_pod(native(prefix + 'pods-after.json'), after)
    assert before['metadata']['uid'] == after['metadata']['uid'] and before['spec'] == after['spec']
    assert a['metadata']['uid'] == b['metadata']['uid'] == report['model_pod_uid']
    assert a['status']['containerStatuses'][0]['restartCount'] == b['status']['containerStatuses'][0]['restartCount']
    assert native(prefix + 'shared-model-before.json')['spec'] == native(prefix + 'shared-model-after.json')['spec']
    assert report['retained_terminal_pods'][0]['phase'] == 'Failed'
    view = 'native/llm-ab-view-30a/'; panels = native(view + 'panels.json'); assert len(panels) == 8
    for panel in panels:
        def values(kind):
            result = native(view + 'panel-' + str(panel['id']) + '-' + kind + '.json')
            assert result['status'] == 'success'
            return {v['metric']['variant']: float(v['value'][1]) for v in result['data']['result']}
        assert values('grafana') == values('prometheus') == {k: float(v) for k, v in panel['values'].items()}
    assert read(base, 'dashboard.json')['passed'] and read(base, 'deployment.json')['authenticated_primary_generation_status'] == 200
    units = [('iot-llm-ab-models-deploy-30b', 'b8887687520943269373e0b7b4211a39'),
             ('iot-llm-model-study-30c', '4ca1c03e9dad47c6b759966e48debfe9'),
             ('iot-llm-ab-view-30a', '6d270dc365aa4585828e700d4cc4c70e')]
    for unit, invocation in units:
        journal = [json.loads(v) for v in bodies['units/' + unit + '/journal.stdout'].splitlines()]
        own = [v for v in journal if v.get('INVOCATION_ID') == invocation]
        assert any('Started ' in v.get('MESSAGE', '') for v in own) and any('Deactivated successfully.' in v.get('MESSAGE', '') for v in own)
    for photo in read(base, 'ui/index.json')['files']:
        body = (base / 'ui' / photo['file']).read_bytes()
        assert body.startswith(b'\xff\xd8\xff') and len(body) == photo['bytes'] and sha256(body).hexdigest() == photo['sha256']
    assert not any(n.startswith('native/llm-model-study-30a/measured-') for n in bodies)
    assert 'native/llm-model-study-30b/warmup--1-0-small.sse' in bodies
    assert 'native/llm-model-study-30b/requests.json' not in bodies
    print(json.dumps({'passed': True, 'native_payloads': 484, 'actual_requests': 32, 'measured_requests': 24,
        'distinct_cases': 4, 'correct_contract_answers': {'small': 3, 'large': 9}, 'grafana_vector_matches': 8,
        'original_ui_captures': 2, 'scope': 'Narrow model-artifact experiment; no agent-configuration or whole-coursework completion claim.'}, indent=2))


if __name__ == '__main__': verify()
