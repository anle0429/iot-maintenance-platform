"""Recompute the mentor follow-up from original native requests, graphs, mutation exits and scaling observations."""
from collections import Counter
from datetime import datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import importlib.util
import json
import re
import sys
import tarfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents.answer_acceptance import verify_specialist
from agents.delegation_acceptance import verify_coordinator
from agents.ci_contract import release_identity
from agents.prompt_registry import prompt_pins
from infra.mcp_scaling import evaluate
from infra.substrate_comparison import summarize
from integration_evidence import capsule
from git_sources import git_blobs

MANIFEST_SHA256 = '588fdb4b9710e1e819375c80ca3b2a687356b1c29f2026fc96812c6cec6d2152'
REVISION = 'f12ea06da2e039d139638c154470f896ed23a7cf'


def module(name, path):
    """Load a standard-library-only checker without starting its native workload entry point."""
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def trace_graph(raw, client, response):
    """Require a genuine terminal client span, named services and closure of every original parent reference."""
    spans = {}; services = Counter()
    allowed = {'iot_coordinator', 'iot_retrieval', 'iot_drift', 'kagent-controller',
               'iot_agentgateway', 'iot_substrate_gateway', 'iot-a2a-client'}
    for line in raw.splitlines():
        for resource in json.loads(line)['result']['resourceSpans']:
            attributes = {a['key']: a['value'].get('stringValue') for a in resource['resource']['attributes']}
            service = attributes.get('service.name')
            if service not in allowed: raise ValueError('Unidentified service remains in the new graph')
            for scope in resource['scopeSpans']:
                for span in scope['spans']:
                    if (span['traceId'] != client['trace_id'] or span['spanId'] in spans
                            or int(span['endTimeUnixNano']) < int(span['startTimeUnixNano'])):
                        raise ValueError('Duplicate, unrelated or invalid original span')
                    spans[span['spanId']] = (service, span); services[service] += 1
    missing = [span['parentSpanId'] for _, span in spans.values() if span.get('parentSpanId') and span['parentSpanId'] not in spans]
    roots = [identity for identity, (_, span) in spans.items() if not span.get('parentSpanId')]
    if missing or roots != [client['span_id']]: raise ValueError('The original graph is incomplete or has a synthetic disconnected root')
    service, root = spans[client['span_id']]
    state = response['result']['status']['state']; expected = {'failed': 2, 'completed': 1}[state]
    attributes = {a['key']: a['value'].get('stringValue', a['value'].get('intValue')) for a in root['attributes']}
    if (service != 'iot-a2a-client' or root['status'].get('code') != expected or not client['exported']
            or client['status_code'] != expected or client['task_id'] != response['result']['id']
            or attributes.get('gen_ai.task.id') != client['task_id'] or attributes.get('a2a.task.state') != state):
        raise ValueError('Automatic client outcome does not match the actual terminal task')
    return {'state': state, 'task_id': client['task_id'], 'trace_id': client['trace_id'], 'status_code': expected,
            'spans': len(spans), 'services': dict(services), 'missing_parents': []}


def verify():
    """Authenticate every original byte and independently reconcile the accepted current follow-up."""
    base, public_count = capsule('mentor-runtime-20261107', MANIFEST_SHA256)
    capture = json.loads((base / 'capture.json').read_bytes()); archive = (base / 'native-receipts.tar.gz').read_bytes()
    assert len(archive) == capture['archive_bytes'] and sha256(archive).hexdigest() == capture['archive_sha256']
    bodies = {}
    with tarfile.open(fileobj=BytesIO(archive)) as package:
        for member in package:
            assert member.isfile() and not Path(member.name).is_absolute() and '..' not in Path(member.name).parts
            assert member.name not in bodies
            bodies[member.name] = package.extractfile(member).read()
    inventory = json.loads(bodies.pop('manifest.json'))
    assert set(bodies) == set(inventory['files']) and len(bodies) == capture['native_payloads']
    assert sha256((json.dumps(inventory, indent=2) + '\n').encode()).hexdigest() == capture['manifest_sha256']
    for name, pin in inventory['files'].items():
        assert len(bodies[name]) == pin['bytes'] and sha256(bodies[name]).hexdigest() == pin['sha256'], name
    assert capture['credential_scan_passed'] and capture['image_layers_and_credentials_excluded']
    selected = json.loads((base / 'selected-payloads.json').read_bytes())
    for name, original in selected['selected_original_native_paths'].items():
        assert (base / name).read_bytes() == bodies['native/' + original]

    def original(name):
        """Decode one authenticated original native JSON payload."""
        return json.loads(bodies['native/' + name])

    source_packets = {}
    for name in bodies:
        if name.startswith('source/') and name.endswith('/manifest.json') and name.count('/') == 2:
            seal = json.loads(bodies[name]); prefix = name.removesuffix('manifest.json') + 'tree/'
            for path, digest in seal['files'].items(): assert sha256(bodies[prefix + path]).hexdigest() == digest
            source_packets[name.split('/')[1]] = seal
    # Bind current business logic to the tested source; earlier in-flight helper/test revisions stay historical.
    checks = {
        'observer-followup-20261106c': [n for n in source_packets['observer-followup-20261106c']['files'] if n.startswith('llm_observability/')],
        'worker-healthy-20261106b': ['scripts/benchmark-agent-scaling.py', 'agents/probe.py', 'agents/tracing.py'],
        'substrate-matched-20261106': ['scripts/benchmark-substrate-control.py', 'infra/substrate_comparison.py', 'agents/probe.py'],
    }
    names = sorted({name for values in checks.values() for name in values}); blobs = git_blobs(ROOT, REVISION, names)
    for packet, names in checks.items():
        for name in names:
            assert bodies['source/' + packet + '/tree/' + name].replace(b'\r\n', b'\n') == blobs[name], name
            assert (ROOT / name).read_bytes().replace(b'\r\n', b'\n') == blobs[name], name

    quality_prefix = 'llm-observer-followup-20261106c/quality-proof/run/'
    quality = original(quality_prefix + 'verification.json'); coverage = original(quality_prefix + 'coverage.json')['totals']
    assert coverage == quality['coverage'] and coverage['excluded_lines'] == 0
    assert coverage['covered_lines'] / coverage['num_statements'] > .9 and coverage['covered_branches'] / coverage['num_branches'] > .9
    stats = original(quality_prefix + 'mutmut-cicd-stats.json'); exits = original(quality_prefix + 'mutation-raw-exits.json')
    meta = {}
    for name in bodies:
        if name.startswith('native/' + quality_prefix + 'mutation-meta/') and name.endswith('.meta'):
            item = json.loads(bodies[name]); assert not item.get('type_check_error_by_key')
            assert not set(meta) & set(item['exit_code_by_key']); meta.update(item['exit_code_by_key'])
    assert meta == exits and Counter(exits.values()) == {1: 2387, 0: 587}
    gate = module('followup_observer_gate', 'scripts/test-llm-observer.py')
    score = gate.mutation_gate(stats, bodies['native/' + quality_prefix + 'mutmut-all-results.txt'].decode(), exits)
    assert score == quality['strict_mutation_score_percent'] > 80 and stats == quality['mutation']
    junit = ET.fromstring(bodies['native/' + quality_prefix + 'junit.xml'])
    assert not any(list(junit.iter(tag)) for tag in ['failure', 'error', 'skipped'])
    installed = original('observer-outcomes-install-20261106c/verification.json')
    assert installed['passed'] and installed['image'].endswith('3d5f8877876c02dcdbed153788c20ea5e452124dd3ea510e4af4c857ec19e6e1')

    prefix = 'a2a-outcomes-joined-20261106/'; graphs = []
    for state in ['failed', 'completed']:
        graphs.append(trace_graph(bodies['native/' + prefix + state + '-jaeger.jsonl'],
                                  original(prefix + state + '-client-trace.json'), original(prefix + state + '-response.json')))
    assert [g['spans'] for g in graphs] == [10, 44]
    children = {role: {kind: original(prefix + 'completed-' + role + '-' + kind + '.json')
                       for kind in ['session', 'tasks']} for role in ['retrieval', 'drift']}
    accepted = verify_coordinator(original(prefix + 'completed-response.json'), children, knowledge_root=ROOT / 'knowledge')
    assert accepted == original(prefix + 'completed-acceptance.json')

    mcp_prefix = 'mcp-scale-20261106b/'; requests = {'retrieval': {'success': 0, 'failure': 0}, 'drift': {'success': 0, 'failure': 0}}
    for name, raw in bodies.items():
        if name.startswith('native/' + mcp_prefix + 'requests/') and name.endswith('-transport.json'):
            transport = json.loads(raw); role = next(r for r in requests if '-' + r + '-' in name)
            assert transport['http'] == 200 and transport['validated'] and transport['error'] is None
            reply = json.loads(bodies[name.removesuffix('-transport.json') + '.json'])
            if role == 'retrieval':
                assert len(reply['matches']) == 3
                for match in reply['matches']:
                    source = (ROOT / 'knowledge' / match['source_file']).read_bytes()
                    assert sha256(source).hexdigest() == match['source_sha256'] and match['text'] in source.decode()
            else: assert reply['unique_rows'] == 32
            requests[role]['success'] += 1
    assert requests == original(mcp_prefix + 'request-counts.json')
    mcp = evaluate(original(mcp_prefix + 'observations.json'), requests)
    assert mcp['roles'] == original(mcp_prefix + 'verification.json')['roles']

    burst = module('followup_worker_burst', 'scripts/benchmark-agent-scaling.py')
    worker_prefix = 'worker-scale-healthy-20261106b/'; history = original(worker_prefix + 'observations.json')
    active_pods = {role: set() for role in burst.ROLES}
    for observation in history:
        path = worker_prefix + observation['prefix'] + '/'
        pods = original(path + 'pods.json')['items']
        metrics = burst.pool_metrics(original(path + 'pools.json')['items'], pods, original(path + 'actors.json')['data']['actors'])
        assert metrics == observation['roles']
        assert burst.scaler_health(original(path + 'scalers.json')['items']) == observation['scaler_health']
        for role in burst.ROLES:
            ready = {p['metadata']['name'] for p in pods if p['metadata']['uid'] in metrics[role]['ready_worker_uids']}
            active_pods[role].update(ready & set(metrics[role]['active_worker_names']))
    for edge in [history[0], history[-1]]:
        assert all(v['desired'] == v['ready'] == 2 and v['running'] == 0 for v in edge['roles'].values())
    maximum = {role: {field: max(r['roles'][role][field] for r in history) for field in ['desired', 'ready', 'running']} for role in burst.ROLES}
    worker = original(worker_prefix + 'verification.json'); assert maximum == worker['maxima'] and all(v['ready'] == 4 for v in maximum.values())
    assert all(len(names) == 4 for names in active_pods.values())
    task_rows = original(worker_prefix + 'requests.json'); assert len(task_rows) == worker['verified_coordinator_requests'] == 11
    for row in task_rows:
        path = worker_prefix + row['path'] + '/'
        children = {role: {kind: original(path + role + '-' + kind + '.json') for kind in ['session', 'tasks']} for role in ['retrieval', 'drift']}
        result = verify_coordinator(original(worker_prefix + row['final_response_path']), children, knowledge_root=ROOT / 'knowledge')
        assert result == original(path + 'acceptance.json') and result['passed']
    assert sum(r['capacity_rejections'] for r in task_rows) == worker['retryable_capacity_rejections'] == 5

    compare_prefix = 'substrate-matched-20261106/'; pairs = original(compare_prefix + 'results.json'); comparison = summarize(pairs)
    for row in pairs:
        reply = original(compare_prefix + str(row['pair']) + '-' + row['arm'] + '-response.json')
        assert verify_specialist(reply, 'retrieval', knowledge_root=ROOT / 'knowledge') == row['acceptance']
    report = original(compare_prefix + 'verification.json')
    assert all(report[k] == v for k, v in comparison.items())
    assert comparison['substrate_percent_change'] > 0  # Preserve the observed slower result.

    gateway = original('gateway-default-helm-20261106b/verification.json'); assert gateway['passed'] and gateway['stable_gateway_references']
    model = json.loads(bodies['current/default-model.json']); assert model['spec']['openAI']['baseUrl'].endswith(':18080/v1')
    agents = json.loads(bodies['current/agents.json'])['items']
    for agent in agents:
        if agent['metadata']['name'] in {'iot-retrieval', 'iot-drift', 'iot-coordinator'}:
            declarative = agent['spec']['declarative']; assert declarative['modelConfig'] == 'default-model-config'
            expected = {'iot-retrieval': 'iot-gateway-retrieval-tools', 'iot-drift': 'iot-gateway-drift-tools'}.get(agent['metadata']['name'])
            if expected: assert declarative['tools'][0]['mcpServer']['name'] == expected

    ci_capture = json.loads((base / 'ci-capture.json').read_bytes()); ci_raw = (base / 'ci-receipts.tar.gz').read_bytes()
    assert len(ci_raw) == ci_capture['archive_bytes'] and sha256(ci_raw).hexdigest() == ci_capture['archive_sha256']
    with tarfile.open(fileobj=BytesIO(ci_raw)) as package:
        ci = {m.name: package.extractfile(m).read() for m in package if m.isfile()}
    ci_inventory = json.loads(ci.pop('manifest.json')); assert set(ci) == set(ci_inventory['files'])
    for name, pin in ci_inventory['files'].items(): assert len(ci[name]) == pin['bytes'] and sha256(ci[name]).hexdigest() == pin['sha256']
    with tarfile.open(base / 'ci-source-packet.tar.gz') as package:
        source = {m.name: package.extractfile(m).read() for m in package if m.isfile()}
    seal = json.loads(source.pop('manifest.json')); assert seal['commit'] == REVISION and len(seal['files']) == 385
    assert set(source) == {'tree/' + n for n in seal['files']}
    for name, digest in seal['files'].items(): assert sha256(source['tree/' + name]).hexdigest() == digest
    provenance = json.loads((base / 'ci-source-provenance.json').read_bytes())
    historical = set(provenance['historical_evidence_files']); tracked = set(seal['files']) - historical
    assert len(tracked) == provenance['tracked_git_files'] == 127
    for name, body in git_blobs(ROOT, REVISION, sorted(tracked)).items(): assert body == source['tree/' + name]
    evidence_index = json.loads((ROOT / 'docs/evidence-archive/manifest.json').read_bytes())
    for name in historical: assert evidence_index['files'][name]['sha256'] == seal['files'][name]
    digest = sha256(json.dumps(seal, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    identity = release_identity('coordinator', '14', REVISION)
    admission = json.loads(ci['release14/admission.json'])
    assert admission['identity'] == identity and admission['source_digest'] == digest and admission['budget']['passed']
    assert admission['budget']['seconds_before_actual_stop'] > admission['budget']['required_seconds'] == 5400
    for phase in ['test', 'prompts', 'build', 'policy', 'deploy', 'verify']:
        row = json.loads(ci['release14/' + phase + '.json'])
        assert row['schema_version'] == 1 and row['phase'] == phase and row['passed'] is True
        assert all(row[key] == value for key, value in identity.items())
        if 'source_digest' in row: assert row['source_digest'] == digest
    expected_prompts = prompt_pins(json.loads(source['tree/agents/response-policy.json'])['prompts'], dict.fromkeys(['retrieval', 'drift', 'coordinator'], 1))
    assert json.loads(ci['release14/prompts.json'])['prompts'] == expected_prompts
    build = json.loads(ci['ci14/build.json']); assert build['number'] == 14 and build['result'] == 'SUCCESS' and not build['building']
    assert ci['ci14/console.txt'].rstrip().endswith(b'Finished: SUCCESS')
    assert json.loads(ci['ci13/build.json'])['result'] == 'FAILURE' and b'Permission denied' in ci['ci13/console.txt']
    parent = json.loads(ci['release14/agent-request/coordinator-response.json'])
    children = {role: {kind: json.loads(ci['release14/agent-request/' + role + '-' + kind + '.json'])
                       for kind in ['session', 'tasks']} for role in ['retrieval', 'drift']}
    ci_acceptance = verify_coordinator(parent, children, knowledge_root=ROOT / 'knowledge')
    assert ci_acceptance == json.loads(ci['release14/agent-acceptance.json'])
    client = json.loads(ci['release14/agent-request/coordinator-client-trace.json'])
    assert client['exported'] and client['status_code'] == 1 and client['task_id'] == parent['result']['id']
    assert json.loads(ci['after-ci/model.json'])['spec'] == model['spec']
    post_capture = json.loads((base / 'post-ci-capture.json').read_bytes()); post_raw = (base / 'post-ci-receipts.tar.gz').read_bytes()
    assert len(post_raw) == post_capture['archive_bytes'] and sha256(post_raw).hexdigest() == post_capture['archive_sha256']
    with tarfile.open(fileobj=BytesIO(post_raw)) as package:
        post = {m.name: package.extractfile(m).read() for m in package if m.isfile()}
    post_inventory = json.loads(post.pop('manifest.json')); assert set(post) == set(post_inventory['files'])
    for name, pin in post_inventory['files'].items(): assert len(post[name]) == pin['bytes'] and sha256(post[name]).hexdigest() == pin['sha256']
    assert b'Result=success' in post['process/unit.txt'] and b'ExecMainStatus=0' in post['process/unit.txt']
    assert b"NameError: name 'seal' is not defined" in post['process/post-ci-a2a.log']
    post_graphs = []
    for state in ['failed', 'completed']:
        post_graphs.append(trace_graph(post['accepted/' + state + '-jaeger.jsonl'],
                          json.loads(post['accepted/' + state + '-client-trace.json']), json.loads(post['accepted/' + state + '-response.json'])))
    post_children = {role: {kind: json.loads(post['accepted/completed-' + role + '-' + kind + '.json'])
                            for kind in ['session', 'tasks']} for role in ['retrieval', 'drift']}
    assert verify_coordinator(json.loads(post['accepted/completed-response.json']), post_children, knowledge_root=ROOT / 'knowledge') == json.loads(post['accepted/completed-acceptance.json'])
    assert json.loads(post['current/model.json'])['spec'] == model['spec']
    stopped = {}
    for role in ['cpu', 'gpu']:
        state = json.loads((base / (role + '-stopped.json')).read_bytes())
        assert state['status'] == 'TERMINATED' and state['scheduling']['instanceTerminationAction'] == 'STOP'
        assert int(state['scheduling']['maxRunDuration']['seconds']) == 28800
        start, end = [datetime.fromisoformat(state[k]) for k in ['lastStartTimestamp', 'lastStopTimestamp']]
        assert timedelta(0) < end - start < timedelta(hours=8)
        stopped[role] = state['lastStopTimestamp']
    return {'passed': True, 'native_payloads': len(bodies), 'public_payloads': public_count, 'observer_mutation_score': score,
            'graphs': graphs, 'mcp': mcp, 'worker': worker, 'worker_active_pod_names': {r: sorted(v) for r, v in active_pods.items()},
            'substrate': comparison,
            'jenkins': {'build': 14, 'result': 'SUCCESS', 'source_files': 385, 'prompt_versions': expected_prompts,
                        'accepted_coordinator_task': parent['result']['id'], 'shared_gateway_model_unchanged': True},
            'post_ci_graphs': post_graphs, 'vms_stopped': stopped,
            'source_revision': REVISION, 'scope': 'Recorded bounded native follow-up; current grading, future uptime and learner understanding are separate.'}


if __name__ == '__main__': print(json.dumps(verify(), indent=2))
