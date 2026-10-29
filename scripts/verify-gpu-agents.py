"""Authenticate native GPU routing, bounded tool turns and real coordinator/child MCP conversations offline."""
from datetime import datetime
from hashlib import sha256
import json
import sys
import zipfile
from integration_evidence import ROOT, capsule, read

sys.path.insert(0, str(ROOT))
from agents.answer_acceptance import verify_specialist
from agents.delegation_acceptance import verify_coordinator

MANIFEST_SHA256 = '0abdbfa0787b041fcde0487995500c216849ea009dada0cff83b23160043d2d1'


def current_sources(base, name):
    """Require every primary native source byte to match the maintained repository, not merely its own snapshot."""
    source = base / 'cpu/source' / name
    seal = read(source, 'manifest.json')
    for name, digest in seal['files'].items():
        snapshot = (source / 'tree' / name).read_bytes()
        assert sha256(snapshot).hexdigest() == digest
        assert sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    return seal


def process_report(base, unit, invocation, report):
    """Bind printed native stdout to its original invocation even after systemd garbage-collects a transient unit."""
    folder = base / 'cpu/process'
    rows = [json.loads(line) for line in (folder / (unit + '.journal.jsonl')).read_bytes().splitlines()]
    stdout = '\n'.join(row['MESSAGE'] for row in rows if row.get('_SYSTEMD_INVOCATION_ID') == invocation
        and row.get('SYSLOG_IDENTIFIER') == 'python')
    assert json.loads(stdout) == report, unit
    assert any('Deactivated successfully' in row.get('MESSAGE', '') for row in rows), unit


def strict_mutation(base):
    """Count only actual named failing tests as kills; keep all 140 mutants, including eight non-test failures, in the denominator."""
    folder = base / 'strict'; report = read(folder, 'report.json'); stats = report['stats']
    assert stats['totalMutantsCount'] == 140 and stats['killedCount'] == 116 and stats['escapedCount'] == 16
    assert stats['skippedCount'] == 8 and stats['errorCount'] == stats['timeOutCount'] == 0
    expected_source = (ROOT / 'tools/model-control/main.go').read_bytes()
    # Some engine versions store skipped outcomes separately; match every actually executed mutant below.
    for items in report.values():
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and 'mutator' in item:
                    assert item['mutator']['originalSourceCode'].encode() == expected_source
    verification = read(folder, 'verification.json')
    assert verification['strict_test_receipts'] and verification['all_default_mutators']
    assert verification['excluded_functions'] == [] and verification['mutated_file'] == 'main.go'
    assert verification['exec_source_sha256'] == sha256((ROOT / 'scripts/go-mutation-exec.py').read_bytes()).hexdigest()
    assert (folder / 'exec.py').read_bytes() == (ROOT / 'scripts/go-mutation-exec.py').read_bytes()
    journal = [json.loads(line) for line in (folder / 'journal.jsonl').read_bytes().splitlines()]
    printed = '\n'.join(row['MESSAGE'] for row in journal if row.get('_SYSTEMD_INVOCATION_ID') == '1ef139c145ad49a496731103f89b363c'
        and row.get('SYSLOG_IDENTIFIER') == 'python')
    assert json.loads(printed) == verification
    assert any('Deactivated successfully' in row.get('MESSAGE', '') for row in journal)
    with zipfile.ZipFile(folder / 'mutant-test-streams.zip') as package:
        rows = [json.loads(line) for line in package.read('records.jsonl').splitlines()]
        assert len(rows) == len({row['mutant_sha256'] for row in rows}) == 140
        by_mutant = {row['mutant_sha256']: row for row in rows}
        for category, reason in [('killed', 'killed_by_failing_test'), ('escaped', 'survived')]:
            for item in report[category]:
                pin = sha256(item['mutator']['mutatedSourceCode'].encode()).hexdigest()
                assert by_mutant[pin]['reason'] == reason
        expected_files = {'records.jsonl'} | {row['mutant_sha256'] + '.' + tail for row in rows for tail in ['stdout', 'stderr']}
        assert set(package.namelist()) == expected_files
        killed = survived = non_test = 0
        for row in rows:
            for tail in ['stdout', 'stderr']:
                body = package.read(row['mutant_sha256'] + '.' + tail)
                assert sha256(body).hexdigest() == row[tail + '_sha256']
            events = [json.loads(line) for line in package.read(row['mutant_sha256'] + '.stdout').splitlines() if line.strip()]
            failing_tests = [event for event in events if event.get('Action') == 'fail' and event.get('Test')]
            ran = {event['Test'] for event in events if event.get('Action') == 'run' and event.get('Test')}
            if row['reason'] == 'killed_by_failing_test':
                assert row['native_test_exit'] != 0 and row['engine_exit'] == 0 and failing_tests
                killed += 1
            elif row['reason'] == 'survived':
                assert row['native_test_exit'] == 0 and row['engine_exit'] == 1 and len(ran) >= 14
                survived += 1
            else:
                assert row['reason'] == 'compilation_or_test_harness_failure' and row['engine_exit'] == 2 and not failing_tests
                non_test += 1
        assert (killed, survived, non_test) == (116, 16, 8)
    assert killed / len(rows) > .8
    blocks = [line.rsplit(' ', 2) for line in (folder / 'coverage.out').read_text().splitlines()[1:]]
    total = sum(int(row[1]) for row in blocks)
    covered = sum(int(row[1]) for row in blocks if int(row[2]) > 0)
    assert covered / total > .9
    return {'killed_by_tests': killed, 'survived': survived, 'non_test_failures_in_denominator': non_test,
        'total_mutants': len(rows), 'strict_mutation_percent': killed / len(rows) * 100,
        'statement_coverage_percent': covered / total * 100}


def verify():
    """Verify the recorded checkpoint while retaining explicit failures and open sandbox/full-coursework acceptance."""
    base, count = capsule('gpu-agents', MANIFEST_SHA256)
    assert count == 577
    curation = read(base, 'curation.json')
    assert curation['actual_limited_join_credential_scan_passed']
    omitted = {row['path']: row for row in curation['excluded_nonessential']}
    for machine in ['cpu', 'gpu']:
        for name, row in read(base / machine, 'inventory.json')['files'].items():
            path = base / machine / name
            if path.exists():
                assert len(path.read_bytes()) == row['bytes'] and sha256(path.read_bytes()).hexdigest() == row['sha256']
            else:
                assert omitted[machine + '/' + name]['sha256'] == row['sha256']
                assert omitted[machine + '/' + name]['bytes'] == row['bytes']
    source_counts = {run: len(current_sources(base, run)['files']) for run in ['gpu-backend-29b', 'model-control-29e', 'agent-conversations-29e']}
    assert source_counts == {'gpu-backend-29b': 11, 'model-control-29e': 10, 'agent-conversations-29e': 25}
    for name, pin in read(base / 'gpu/source', 'manifest.json')['files'].items():
        if name == 'bootstrap.py': continue
        assert sha256((base / 'gpu/source/tree' / name).read_bytes()).hexdigest() == pin
        assert sha256((ROOT / name).read_bytes()).hexdigest() == pin
    cpu = base / 'cpu/native'; gpu = cpu / 'gpu-backend-29b'; control = cpu / 'model-control-29e'
    deployment = read(gpu, 'verification.json')
    assert deployment['passed'] and deployment['routed_gpu_verified'] and deployment['controlled_atomic_rollbacks'] == 1
    process_report(base, 'iot-gpu-backend-29b', '9fed3ac093a3414cbd00a6a36fb16fb1', deployment)
    node = read(gpu, 'node-before.stdout')
    assert node['metadata']['uid'] == deployment['gpu_node'] and node['metadata']['name'] == 'iot-cw-gpu'
    assert node['metadata']['labels']['iot.coursework/role'] == 'inference-gpu'
    assert {'key': 'iot.coursework/inference-gpu', 'value': 'true', 'effect': 'NoSchedule'} in node['spec']['taints']
    assert 'Tesla V100-SXM2-16GB, 580.178.04, 16384 MiB' in (gpu / 'candidate-gpu.stdout').read_text()
    assert read(gpu, 'candidate-history.stdout')[-1]['description'].startswith('Rollback to ')
    assert int((gpu / 'candidate-bad-image.exit').read_text()) != 0
    assert 'ErrImageNeverPull' in (gpu / 'candidate-events-after-rollback.stdout').read_text()
    actual_node = next(n for n in read(base / 'cpu/readback', 'nodes.json')['items'] if n['metadata']['name'] == 'iot-cw-gpu')
    assert actual_node['status']['allocatable']['nvidia.com/gpu'] == '1'
    actual_pods = [p for p in read(base / 'cpu/readback', 'inference-resources.json')['items'] if p['kind'] == 'Pod']
    model_pod = next(p for p in actual_pods if p['metadata']['labels'].get('app.kubernetes.io/instance') == 'iot-model-gpu')
    assert model_pod['spec']['nodeName'] == 'iot-cw-gpu'
    assert model_pod['spec']['containers'][0]['resources']['limits']['nvidia.com/gpu'] == '1'
    stream_count = 0
    for name in ['direct.json', 'after-rollback.json', 'routed.json']:
        streams = read(gpu, name); assert len(streams) == 3
        for row in streams:
            assert row['status'] == 200 and row['usage']['prompt_tokens_details']['cached_tokens'] == 0 and row['text']
            assert {event.get('system_fingerprint') for event in row['events']} == {'b11977-7ac5727d6'}
            stream_count += 1
    assert read(gpu, 'model-config-before.stdout')['spec'] == read(gpu, 'model-config-after.stdout')['spec']
    adapter = read(control, 'verification.json'); assert adapter['passed'] and not adapter['response_rewriting']
    process_report(base, 'iot-model-control-29e', '33b589c58343424fa0fc60fa9a6311fd', adapter)
    assert adapter['image'] == 'docker.io/library/iot-model-control@sha256:6001ff60ce56e13e9c891279cbc37ee9a899fbf73d856b01e2a9b36655a3f063'
    for mode in ['direct', 'routed']:
        for number in range(3):
            request = read(control, mode + '-' + str(number) + '-request.json')
            response = read(control, mode + '-' + str(number) + '-response.json')
            assert 'stop' not in request and request['parallel_tool_calls'] is False
            choice = response['choices'][0]; assert choice['finish_reason'] == 'tool_calls' and len(choice['message']['tool_calls']) == 1
            assert isinstance(json.loads(choice['message']['tool_calls'][0]['function']['arguments']), dict)
            assert response['system_fingerprint'] == 'b11977-7ac5727d6'
    protocol = read(cpu / 'agent-chat-29m', 'verification.json')
    process_report(base, 'iot-agent-chat-29m', '9a2766df799641c5b44f24125382084f', protocol)
    answers = {role: verify_specialist(read(cpu / 'agent-chat-29m', role + '-response.json'), role) for role in ['retrieval', 'drift']}
    children = {role: {kind: read(cpu / 'agent-children-29b', role + '-' + kind + '.json') for kind in ['session', 'tasks']} for role in ['retrieval', 'drift']}
    answers['coordinator'] = verify_coordinator(read(cpu / 'agent-chat-29m', 'coordinator-response.json'), children)
    assert answers == read(base / 'local', 'acceptance-29m.json')
    assert [answers[role]['successful_tool_events'] for role in ['retrieval', 'drift']] == [3, 1]
    lease = read(cpu / 'agent-feature-29d', 'verification.json')
    assert lease['passed'] and lease['machines'] == 200 and lease['ready']['clock_mode'] == 'simulation'
    for role in ['retrieval', 'drift', 'coordinator']:
        task = read(cpu / 'agent-chat-29m', role + '-response.json')['result']
        assert datetime.fromisoformat(lease['prepared_wall_utc']) < datetime.fromisoformat(task['status']['timestamp']) < datetime.fromisoformat(lease['lease_expires_utc'])
    catalog = read(cpu / 'agent-catalog-29c', 'verification.json')
    assert catalog['passed'] and len(catalog['entries']) == 3 and {e['version'] for e in catalog['entries']} == {'0.2.2'}
    actual_agents = {a['metadata']['name']: a for a in read(base / 'cpu/readback', 'agents.json')['items'] if a['kind'] == 'SandboxAgent'}
    for entry in catalog['entries']:
        agent = actual_agents[entry['name']]
        assert entry['resource_uid'] == agent['metadata']['uid']
        assert entry['spec_sha256'] == sha256(json.dumps(agent['spec'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    failed_probe = read(cpu / 'agent-isolation-exercise-29b/retrieval-probe', 'verification.json')
    assert not failed_probe['passed'] and failed_probe['actual_probe'] is None
    raw_probe = json.loads((cpu / 'agent-isolation-exercise-29b/retrieval-probe/diagnostic.stdout').read_bytes())
    assert raw_probe['filesystem_boundary_verified'] and not raw_probe['egress_policy_verified']
    assert raw_probe['pid1_uid'] == 0 and raw_probe['blocked_other_role_mcp']['http_status'] == 200
    quality = strict_mutation(base)
    return {'passed': True, 'payloads_verified': count, 'current_native_sources': source_counts,
        'gpu_route_streams': stream_count, 'specialist_answers_verified': True, 'coordinator_delegation_verified': True,
        'adapter_quality': quality, 'sandbox_egress_verified': False, 'autoscaling_verified': False,
        'coursework_complete': False}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
