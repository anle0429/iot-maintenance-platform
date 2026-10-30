"""Authenticate the fresh learner demonstration, failed predecessor and narrow native monitoring repair."""
from datetime import datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import importlib.util, json, sys, tarfile
from integration_evidence import ROOT, capsule, read
sys.path.insert(0, str(ROOT))
from agents.answer_acceptance import native_tools, verify_specialist
from agents.delegation_acceptance import verify_coordinator
from agents.probe import QUESTIONS
from infra.llm_steady_state import monitoring_configuration, observer_deployment
from scripts.evidence_integrity import source_bindings
from scripts.git_sources import git_blobs

MANIFEST = '00a890476fe64955ee77b9cae1e7aba24a33deaaf5e950f8bf66c4107ef7799b'


def unpack(raw):
    """Check every original member without extracting or accepting archive links or duplicate paths."""
    bodies = {}
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            assert member.isdir() or member.isfile()
            if not member.isfile(): continue
            name = PurePosixPath(member.name).relative_to('learner-demo-capture-30b')
            assert not name.is_absolute() and '..' not in name.parts and member.size < 10000000
            assert str(name) not in bodies
            bodies[str(name)] = archive.extractfile(member).read()
    assert sha256(bodies['manifest.json']).hexdigest() == 'ca1de7ee8d9c71ee8cd91eb9794c58a060af995ec7e9d5c10c338595e0fa4b5a'
    inventory = json.loads(bodies['manifest.json'])
    assert inventory['current_native_credentials_scanned'] and inventory['vault_root_and_unseal_scanned']
    assert not inventory['workloads_rerun_for_capture'] and len(inventory['files']) == 280
    assert set(bodies) == set(inventory['files']) | {'manifest.json'}
    for name, pin in inventory['files'].items():
        assert len(bodies[name]) == pin['bytes'] and sha256(bodies[name]).hexdigest() == pin['sha256'], name
    return bodies


def verify():
    """Recompute answers and model arithmetic, then check actual lease, configuration boundaries and stable observations."""
    base, count = capsule('learner-demo', MANIFEST); assert count == 17
    metadata = read(base, 'native-archive.json'); raw = (base/'native-receipts.tar.gz').read_bytes()
    assert len(raw) == metadata['archive_bytes'] == 230312
    assert sha256(raw).hexdigest() == metadata['archive_sha256'] == 'bf99922f325e9c366b4ba47ba4e0870487f5020b7d06a05f2a40edfff7127ab2'
    assert metadata['pc_actual_gpu_join_credential_scanned'] and not metadata['recompressed_or_rewritten_on_pc']
    assert metadata['current_native_credentials_scanned'] and not metadata['workloads_rerun_for_capture']
    bodies = unpack(raw); native = lambda n: json.loads(bodies[n]); out = lambda n: 'native/'+n+'/'
    for folder, expected in [('learner-demo-f845b5b', 14), ('steady-cd42ddc', 3)]:
        prefix = 'source/'+folder+'/'; seal = native(prefix+'manifest.json')
        assert source_bindings(seal) == expected
        for name, pin in seal['files'].items():
            original = bodies[prefix+'tree/'+name]
            assert sha256(original).hexdigest() == pin
            assert (ROOT/name).read_bytes().replace(b'\r\n', b'\n') == original.replace(b'\r\n', b'\n'), name
    helper = bodies['helpers/iot-refresh-demo-feature-29k.py']
    assert sha256(helper).hexdigest() == 'ec2be13e02a218a5e583c7006ce418648ee8bb1829acb1c5d9b10f89e41552c0'
    assert helper == git_blobs(ROOT, '51b3f10853014b9a33325aa113a01f6697b4aa21', ['scripts/refresh-agent-features.py'])['scripts/refresh-agent-features.py']
    assert (ROOT/'scripts/refresh-agent-features.py').read_bytes().replace(b'\r\n', b'\n') == helper
    feature = native(out('feature-demo-30a')+'verification.json')
    assert feature == read(base, 'feature-verification.json') and feature['passed'] and not feature['resume_deploy']
    prepared = datetime.fromisoformat(feature['prepared_wall_utc']); expiry = datetime.fromisoformat(feature['lease_expires_utc'])
    assert expiry-prepared == timedelta(hours=1) and feature['machines'] == 200 and feature['model_version'] == '6'
    assert native(out('feature-demo-30a')+'physical-ttl-before.json') == {'keys': 0, 'pttl_ms': []}
    ttl = native(out('feature-demo-30a')+'physical-ttl-after.json')
    assert ttl['keys'] == len(ttl['pttl_ms']) == 200 and min(ttl['pttl_ms']) > 600000
    ready = native('feature-state/_READY.json')
    for name in ['context', 'verification']:
        assert ready[name+'_sha256'] == sha256(bodies['feature-state/'+name+'.json']).hexdigest()
    assert datetime.fromisoformat(native('feature-state/context.json')['prepared_wall_utc']) == prepared
    container = native(out('feature-demo-30a')+'materialization-container.json')
    assert container['state']['ExitCode'] == 0 and not container['state']['OOMKilled'] and container['image'] == feature['sdk_image_id']
    assert datetime.fromisoformat(container['state']['StartedAt'].replace('Z', '+00:00')) < prepared
    assert datetime.fromisoformat(container['state']['FinishedAt'].replace('Z', '+00:00')) > prepared
    assert b'REVISION: 50' in bodies[out('feature-demo-30a')+'helm.stdout']
    old = native(out('learner-demo-30a')+'coordinator-response.json')['result']
    old_child = native(out('learner-demo-30a')+'retrieval-tasks-response.json')['data']
    assert old['status']['state'] == 'failed' and len(old_child) == 1 and old_child[0]['status']['state'] == 'failed'
    for task in [old, old_child[0]]:
        assert not task.get('artifacts') and 'failed to find endpoint candidates' in task['status']['message']['parts'][0]['text']
    assert not any(p.get('metadata', {}).get('adk_type') == 'function_response' for m in old_child[0]['history'] for p in m['parts'])
    diagnosis = native(out('observer-health-diagnosis-30a')+'summary.json')
    observer = next(c for c in diagnosis['containers'][0] if c['name'] == 'observer')
    assert observer['restartCount'] == 36 and observer['lastState']['terminated']['exitCode'] == 0
    assert observer['lastState']['terminated']['reason'] == 'Completed' and '1000m' in diagnosis['top']
    assert all(v['status'] == 200 for v in diagnosis['health']) and diagnosis['health'][0]['elapsed'] > 1
    steady = out('llm-steady-state-30a'); before = native(steady+'deployment-before.stdout'); after = native(steady+'deployment-after.stdout')
    baseline = native('baseline/observer.json')
    assert before['spec'] == baseline['spec'] and after['spec'] == observer_deployment(before)['spec']
    assert before['metadata']['uid'] == after['metadata']['uid'] == '208a0ec5-2e41-49ce-90a3-c2e5f0d6d2a9'
    config = native(steady+'prometheus-before.json'); planned = native(steady+'prometheus-planned.json')
    assert planned == monitoring_configuration(config)
    assert bodies[steady+'prometheus-pid-before.stdout'] == bodies[steady+'prometheus-pid-after.stdout'] == b'20990\n'
    rows = native(steady+'observations.json'); assert len(rows) == 13 and rows[-1]['elapsed'] >= 180
    assert len({r['pod_uid'] for r in rows}) == 1
    identities = []
    for i in range(13):
        pods = native(steady+'pods-'+str(i)+'.stdout')['items']
        pods = [v for v in pods if not v['metadata'].get('deletionTimestamp')]; assert len(pods) == 1
        identities.append((pods[0]['metadata']['uid'], tuple((v['name'], v['restartCount']) for v in pods[0]['status']['containerStatuses'])))
        assert all(c['ready'] for c in pods[0]['status']['containerStatuses'])
        assert native(steady+'live-'+str(i)+'.json')['live'] and native(steady+'ready-'+str(i)+'.json')['ready']
    assert len(set(identities)) == 1 and identities[0][0] == rows[0]['pod_uid']
    for kind, number in native(steady+'ledger-before.json').items(): assert native(steady+'ledger-after.json')[kind] >= number
    target = [t for t in native(steady+'targets-after.json')['data']['activeTargets'] if t['labels'].get('job') == 'iot-llm-observer']
    assert len(target) == 1 and target[0]['health'] == 'up' and target[0]['scrapeInterval'] == '15s' and target[0]['scrapeTimeout'] == '10s'
    assert native(steady+'verification.json') == read(base, 'steady-state-verification.json')
    accepted = out('learner-demo-30b'); reply = native(accepted+'coordinator-response.json')
    children = {role: {kind: native(accepted+role+'-'+kind+'-response.json') for kind in ['session', 'tasks']} for role in ['retrieval', 'drift']}
    proof = verify_coordinator(reply, children, knowledge_root=ROOT/'knowledge')
    assert proof == native(accepted+'acceptance.json') == read(base, 'accepted-verification.json')['actual_acceptance']
    request = native(accepted+'coordinator-request.json'); original_request = native(out('learner-demo-30a')+'coordinator-request.json')
    assert request['params']['message']['parts'] == original_request['params']['message']['parts'] == [{'kind': 'text', 'text': QUESTIONS['coordinator']}]
    assert request['params']['message']['contextId'] == reply['result']['contextId'] != old['contextId']
    assert prepared < datetime.fromisoformat(reply['result']['status']['timestamp'].replace('Z', '+00:00')) < expiry
    agent_before = {v['metadata']['uid']: v['spec'] for v in native(accepted+'agents-before.json')['items']}
    assert len(agent_before) == 5 and agent_before == {v['metadata']['uid']: v['spec'] for v in native(accepted+'agents-after.json')['items']}
    total = 0
    for role, expected in [('retrieval', 3), ('drift', 1)]:
        task = children[role]['tasks']['data'][0]; tools, counts = native_tools(task, role)
        assert counts == {'successful_tool_events': expected, 'tool_error_events': 0}; total += expected
        assert verify_specialist({'result': task}, role) == proof['children'][role]
        assert sum(e.get('conditions', {}).get('ready') is True for s in native(accepted+role+'-endpoints-before.json')['items'] for e in s['endpoints']) == 2
    spec = importlib.util.spec_from_file_location('independent_learner_prediction', ROOT/'scripts/demo-feature-api.py')
    arithmetic = importlib.util.module_from_spec(spec); spec.loader.exec_module(arithmetic)
    tools, _ = native_tools(children['retrieval']['tasks']['data'][0], 'retrieval')
    pin = native('models/model-pin.json'); portable = native('models/portable.json')
    assert pin == read(ROOT/'docs/evidence/ci-serving-linux/selected-model6', 'model-pin.json')
    assert sha256(bodies['models/portable.json']).hexdigest() == pin['portable_sha256']
    value = arithmetic.check_prediction(tools['get_machine_features']['output'], tools['predict_failure']['output'], portable, pin,
        datetime.fromisoformat(reply['result']['status']['timestamp'].replace('Z', '+00:00')))
    error = abs(value-tools['predict_failure']['output']['prediction']['risk_score'])
    for name in bodies:
        if not name.startswith(accepted) or not name.endswith('-transport.json'): continue
        transport = native(name)
        assert transport['status'] == 200 and transport['ca_verified'] and transport['hostname_verified'] and not transport['upstream_bypassed']
        assert transport['gateway_host'] == 'agents.iot.test' and transport['leaf_sha256'] == 'e60801a16f3febb05fb3d855b627d7acf43af8df8fde5dd3e7771d1190e8a999'
        assert transport['response_bytes'] == len(bodies[name.replace('-transport.json', '-response.json')])
    for unit, invocation in [('iot-feature-demo-30a', 'e26c1571e7ec455abdf4fe29bb441f81'), ('iot-llm-steady-state-30a', '63e42f4e510844dd90f4fedc9422b6c6'), ('iot-learner-demo-30b', '670f4468a33b4c3a897acff1e2026360')]:
        journal = [json.loads(v) for v in bodies['units/'+unit+'.journal.jsonl'].splitlines()]
        own = [v for v in journal if v.get('INVOCATION_ID') == invocation]
        assert any('Started ' in v.get('MESSAGE', '') for v in own) and any('Deactivated successfully.' in v.get('MESSAGE', '') for v in own)
    for folder in ['learner-demo-preflight-30b', 'steady-state-stage-30a']:
        assert b'Ran 2 tests' in bodies[out(folder)+'tests.stderr'] and b'OK' in bodies[out(folder)+'tests.stderr']
    gpu_seal = read(base, 'gpu-recovery-source.json'); assert source_bindings(gpu_seal) == 2
    gpu_code = (base/'gpu-recovery-source.py').read_bytes()
    assert sha256(gpu_code).hexdigest() == gpu_seal['files']['scripts/resume-gpu-services.py']
    assert (ROOT/'scripts/resume-gpu-services.py').read_bytes().replace(b'\r\n', b'\n') == gpu_code
    gpu = read(base, 'gpu-recovery-verification.json'); assert gpu['passed'] and gpu['new_generation_requests'] == 0
    assert gpu['session'] == native(accepted+'gpu-session.json')
    assert set(gpu['models'].values()) == {'loaded'} and set(gpu['models']) == {'iot-qwen2.5-7b', 'iot-qwen2.5-1.5b'}
    assert datetime.fromisoformat(gpu['auto_stop_utc'])-datetime.fromisoformat(gpu['session']['last_start_utc']) == timedelta(hours=8)
    for photo in read(base, 'ui/index.json')['files']:
        body = (base/'ui'/photo['file']).read_bytes()
        assert body.startswith(b'\xff\xd8\xff') and len(body) == photo['bytes'] and sha256(body).hexdigest() == photo['sha256']
    ax = (base/'ui/accepted-answer.ax.txt').read_text(encoding='utf-8')
    assert reply['result']['contextId'] in ax and '0.103181' in ax and 'c23698fc9426a3c56e85cbd074e529a5a1158fe392b4882505f569cfa8dcfa9a' in ax
    print(json.dumps({'passed': True, 'native_payloads': 280, 'accepted_parent_tasks': 1, 'accepted_child_tasks': 2,
        'native_successful_mcp_events': total, 'failed_parent_and_child_retained': True, 'independent_prediction_error': error,
        'stable_health_samples': 13, 'scope': 'Recorded genuine lease, configuration repair and learner conversation; not current readiness, a benchmark or a grade.'}, indent=2))


if __name__ == '__main__': verify()
