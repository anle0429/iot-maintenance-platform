"""Authenticate original gVisor/network receipts and normal specialist/coordinator behavior without starting services."""
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import importlib.util
import json
import sys
import zipfile
from integration_evidence import ROOT, capsule, read

sys.path.insert(0, str(ROOT))
from agents.answer_acceptance import verify_specialist
from agents.delegation_acceptance import verify_coordinator

MANIFEST_SHA256 = '81bc2d9b373f7bb0e96d0a70f0404f33944a47c654bc4f2d59586041f92e3096'


def verify():
    """Bind source, process invocations, actual worker identities and A/B/A packet evidence to the retained bytes."""
    base, public_count = capsule('agent-network', MANIFEST_SHA256)
    with zipfile.ZipFile(base / 'native-receipts.zip') as package:
        names = package.namelist()
        assert len(names) == len(set(names)) and all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in names)
        assert sum(info.file_size for info in package.infolist()) < 20 * 1024**2
        bodies = {name: package.read(name) for name in names}
    inventory = json.loads(bodies['manifest.json']); archive = read(base, 'native-archive.json')
    assert sha256(bodies['manifest.json']).hexdigest() == archive['original_inventory_sha256']
    assert set(bodies) == set(inventory['files']) | {'manifest.json'}
    assert len(inventory['files']) == archive['native_payloads'] == 392
    for name, row in inventory['files'].items():
        assert len(bodies[name]) == row['bytes'] and sha256(bodies[name]).hexdigest() == row['sha256'], name

    def original(name):
        """Decode one already authenticated original native payload."""
        return json.loads(bodies[name])

    def process(unit, invocation, report, identifier='python'):
        """Match stdout from one actual native invocation and its successful terminal journal event."""
        rows = [json.loads(line) for line in bodies['process/' + unit + '.journal.jsonl'].splitlines()]
        printed = '\n'.join(row['MESSAGE'] for row in rows if row.get('_SYSTEMD_INVOCATION_ID') == invocation and row.get('SYSLOG_IDENTIFIER') == identifier)
        assert json.loads(printed) == report, unit
        assert any('Deactivated successfully' in row.get('MESSAGE', '') for row in rows), unit

    seal = original('source/agent-network-29b/manifest.json')
    for name, digest in seal['files'].items():
        assert sha256(bodies['source/agent-network-29b/tree/' + name]).hexdigest() == digest
        assert sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    assert len(seal['files']) == 4
    for native, current in [('operators/probe-agent-network-29b.py', 'scripts/probe-agent-network.py'),
                            ('operators/probe-agent-isolation.py', 'scripts/probe-agent-isolation.py')]:
        assert bodies[native] == (ROOT / current).read_bytes(), current
    build = read(base / 'pc-probe-build', 'build-info.json'); binary = original('assets/native-binary.json')
    assert build['binary_sha256'] == binary['sha256'] == 'd3c2ccda96495a6e33f877bffb3c88be24136cd2f638c2aa625406fa42449f8b'
    assert build['binary_bytes'] == binary['bytes'] == 7045280 and build['static_elf_no_interpreter']
    for name, digest in build['source_sha256'].items(): assert sha256((ROOT / name).read_bytes()).hexdigest() == digest
    assert int((base / 'pc-probe-build/tests.exit').read_text()) == int((base / 'pc-probe-build/build.exit').read_text()) == 0
    events = [json.loads(line) for line in (base / 'pc-probe-build/tests.jsonl').read_bytes().splitlines()]
    assert sum(e.get('Action') == 'pass' and bool(e.get('Test')) for e in events) == 5
    assert not any(e.get('Action') == 'fail' for e in events)
    spec = importlib.util.spec_from_file_location('network_probe', ROOT / 'scripts/probe-agent-network.py')
    probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)
    installation = original('native/agent-network-29b/verification.json')
    assert installation['passed'] and not installation['actor_egress_acceptance_verified']
    process('iot-agent-network-29b', '1116d609e7c04669a159737e5c8dcbef', installation)
    policies = original('native/agent-network-29b/policies-after.stdout')['items']
    actual = {p['metadata']['name']: p for p in original('current/policies.json')['items'] if p['metadata']['labels'].get('iot.coursework/agent-network') == 'true'}
    assert len(actual) == 3
    for policy in policies: assert actual[policy['metadata']['name']]['spec'] == policy['spec']
    assert not any('probe-positive-control' in p['metadata']['name'] for p in original('current/policies.json')['items'])
    result = original('native/agent-network-probe-29b/verification.json')
    process('iot-agent-network-probe-29b', 'facf2f0fdbc74f6ca1ae513024693dea', result)
    assert result['passed'] and set(result['roles']) == {'retrieval', 'drift'} and not result['coursework_complete']
    phase_count = 0
    for role, data in result['roles'].items():
        prefix = 'native/agent-network-probe-29b/' + role
        request = original(prefix + '/request.json'); context = request['params']['message']['contextId']
        assert data['actor_id'] == probe.actor_id(role, context)
        before = original(prefix + '/actor-before.stdout'); running = original(prefix + '/actor-running.stdout'); after = original(prefix + '/actor-after.stdout')
        assert before['status'] == after['status'] == 'STATUS_SUSPENDED' and running['status'] == 'STATUS_RUNNING'
        assert before['metadata']['uid'] == running['metadata']['uid'] == after['metadata']['uid'] == data['actor_uid']
        worker = original(prefix + '/worker.stdout')
        assert worker['metadata']['uid'] == running['ateomPodUid'] == data['worker_uid']
        assert worker['status']['podIP'] == data['worker_ip']
        assert worker['metadata']['labels']['ate.dev/worker-pool'] == 'iot-' + role + '-pool'
        assert [p['phase'] for p in data['phases']] == ['deny-before', 'allow-control', 'deny-restored']
        for record in data['phases']:
            name = prefix + '/' + record['phase']; measured = original(name + '/acceptance.json')
            assert measured == record and record['passed']
            assert original(name + '/probe/worker.json')['metadata']['uid'] == data['worker_uid']
            assert original(name + '/probe/verification.json')['host_positive_controls'] == {'retrieval': 200, 'drift': 200}
            diagnostic = original(name + '/probe/diagnostic.stdout'); assert diagnostic == record['diagnostic']
            assert diagnostic['pid1_uid'] == 0 and 'gvisor' in diagnostic['kernel']
            assert diagnostic['filesystem'] == {'host_coursework_data': 'absent', 'kubernetes_serviceaccount_token': 'absent'}
            counters = [probe.reject_counters(bodies[name + '/rules-' + suffix + '.stdout'], data['worker_ip']) for suffix in ['before', 'after']]
            assert [counters[0], counters[1]] == [record['counter_before'], record['counter_after']]
            assert {r['chain'] for r in counters[0]['rules']} == {r['chain'] for r in counters[1]['rules']}
            delta = counters[1]['packets'] - counters[0]['packets']; assert delta == record['reject_packet_delta']
            assert probe.phase_accepted(record['phase'], diagnostic, delta)
            # The earlier standalone Go diagnostic accepted HTTP403 only. Preserve its original false result;
            # the stronger enclosing experiment establishes CNI rejection with counters and the A/B/A control.
            assert not diagnostic['egress_policy_verified'] and not diagnostic['passed']
            phase_count += 1
    assert not original('native/agent-network-29a/verification.json')['passed']
    assert b'NotFound' in bodies['native/agent-network-probe-29a/drift/actor-before.stderr']
    protocol = original('native/agent-chat-29n/verification.json')
    assert protocol['passed']; process('iot-agent-chat-29n', 'd462233d4d1649c893678c1841206e29', protocol)
    answers = {role: verify_specialist(original('native/agent-chat-29n/' + role + '-response.json'), role) for role in ['retrieval', 'drift']}
    children = {role: {kind: original('native/agent-children-29c/' + role + '-' + kind + '.json') for kind in ['session', 'tasks']} for role in ['retrieval', 'drift']}
    answers['coordinator'] = verify_coordinator(original('native/agent-chat-29n/coordinator-response.json'), children)
    assert [answers[r]['successful_tool_events'] for r in ['retrieval', 'drift']] == [3, 1]
    lease = original('native/agent-feature-29e/verification.json')
    assert lease['passed'] and lease['machines'] == 200 and lease['ready']['clock_mode'] == 'simulation'
    for role in ['retrieval', 'drift', 'coordinator']:
        task = original('native/agent-chat-29n/' + role + '-response.json')['result']
        assert datetime.fromisoformat(lease['prepared_wall_utc']) < datetime.fromisoformat(task['status']['timestamp']) < datetime.fromisoformat(lease['lease_expires_utc'])
    return {'passed': True, 'public_payloads': public_count, 'original_native_payloads': len(inventory['files']),
            'direct_specialist_mcp_egress_verified': True, 'native_control_phases': phase_count,
            'normal_specialist_and_coordinator_answers_verified': True, 'pid1_uid': 0,
            'nonroot_verified': False, 'autoscaling_verified': False, 'coursework_complete': False}


if __name__ == '__main__': print(json.dumps(verify(), indent=2))
