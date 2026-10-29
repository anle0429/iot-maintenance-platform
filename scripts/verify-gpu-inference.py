"""Authenticate original paired CPU/GPU inference, infrastructure and process receipts without a running cloud VM."""
from hashlib import sha256
from statistics import median
import json
import math
from integration_evidence import ROOT, capsule, read, sources, exited

MANIFEST_SHA256 = '23cc15623f3f123b304c04e6c8ef467e0807b1f966460ccde55614ab3ee77a5e'


def process_report(base, name, invocation, report):
    """Use the original stdout invocation and completion journal even when a transient unit was garbage-collected."""
    unit = (base / 'process' / (name + '.unit.txt')).read_text()
    assert 'ActiveState=inactive\n' in unit and 'Result=success\n' in unit and 'ExecMainStatus=0\n' in unit
    rows = [json.loads(line) for line in (base / 'process' / (name + '.journal.jsonl')).read_text().splitlines()]
    stdout = '\n'.join(row['MESSAGE'] for row in rows if row.get('_SYSTEMD_INVOCATION_ID') == invocation
                       and row.get('SYSLOG_IDENTIFIER') == 'python3')
    assert json.loads(stdout) == report
    assert any('Deactivated successfully' in row.get('MESSAGE', '') for row in rows)


def verify():
    """Recompute paired timings and validate native GPU allocation while keeping agent integration explicitly separate."""
    base, count = capsule('gpu-inference', MANIFEST_SHA256)
    assert count == 160
    source = sources(base, 'benchmark', current=True)
    assert len(source['files']) == 5
    runtime_source = read(base, 'runtime/source/manifest.json')
    assert set(runtime_source['files']) == {'infra/ansible/gpu-runtime.yml', 'infra/ansible/requirements-controller.txt', 'bootstrap.py'}
    for name, pin in runtime_source['files'].items():
        if name == 'bootstrap.py': continue
        assert sha256((base / 'runtime/source/tree' / name).read_bytes()).hexdigest() == pin
        assert sha256((ROOT / name).read_bytes()).hexdigest() == pin
    curation = read(base, 'curation.json')
    assert curation['original_archive_sha256'] == '0480c62ac4aa0a0034904166e212d1e86fb262157c741a7a04c87c67a0c5b980'
    excluded = {row['path']: row for row in curation['excluded_nonessential']}
    assert set(excluded) == {'runtime/source/tree/bootstrap.py',
        'benchmark/source/tree/llm/__pycache__/__init__.cpython-312.pyc',
        'benchmark/source/tree/llm/__pycache__/benchmark.cpython-312.pyc',
        'benchmark/source/tree/llm/__pycache__/gpu_benchmark.cpython-312.pyc'}
    inventory = read(base, 'inventory.json')['files']
    for name, row in inventory.items():
        if name in excluded:
            assert excluded[name]['sha256'] == row['sha256'] and excluded[name]['bytes'] == row['bytes']
        else:
            body = (base / name).read_bytes()
            assert len(body) == row['bytes'] and sha256(body).hexdigest() == row['sha256']
    for name in ['main.tf', '.terraform.lock.hcl']:
        assert (base / 'infrastructure' / name).read_bytes() == (ROOT / 'infra/gcp/inference' / name).read_bytes()
    assert 'No changes. Your infrastructure matches the configuration.' in (base / 'cloud/gpu-worker-29r-noop-plan.txt').read_text()
    vm = read(base, 'cloud/gpu-worker-29t-readback.json')
    assert vm['name'] == 'iot-cw-gpu' and vm['id'] == '11538226507322059' and vm['status'] == 'RUNNING'
    assert vm['machineType'].endswith('/n1-standard-4')
    assert vm['guestAccelerators'] == [{'acceleratorCount': 1, 'acceleratorType': vm['guestAccelerators'][0]['acceleratorType']}]
    assert vm['guestAccelerators'][0]['acceleratorType'].endswith('/nvidia-tesla-v100')
    assert all(not interface.get('accessConfigs') for interface in vm['networkInterfaces'])
    assert vm['scheduling']['instanceTerminationAction'] == 'STOP' and vm['scheduling']['maxRunDuration']['seconds'] == '28800'
    disk = read(base, 'cloud/gpu-disk-29t-readback.json')
    assert disk['sizeGb'] == '50' and disk['type'].endswith('/pd-ssd') and len(disk['users']) == 1
    firewall = read(base, 'cloud/gpu-firewall-29s-readback.json')
    assert set(firewall['sourceRanges']) == {'10.42.0.2/32', '10.42.0.7/32'}
    assert firewall['allowed'] == [{'IPProtocol': 'tcp', 'ports': ['6443', '10250']}, {'IPProtocol': 'udp', 'ports': ['8472']}]
    assert read(base, 'cloud/gpu-a100-quota-status-29b.json')['quotaConfig']['stateDetail'] == 'Quota request denied'
    assert read(base, 'cloud/gpu-cpu-quota-status-29e.json')['quotaConfig']['grantedValue'] == '20'
    assets = read(base, 'assets/native/verification.json'); runtime = read(base, 'runtime/native/verification.json')
    result = read(base, 'benchmark/native/verification.json')
    assert assets['passed'] and assets['actual_model_bytes'] == 4_683_073_632 and runtime['passed'] and result['passed']
    process_report(base, 'iot-gpu-assets-29a', 'd38b582a453d4c4cbc33acb1ae25fbb1', assets)
    process_report(base, 'iot-gpu-runtime-29a', '3b0ab4b9f43642f9b1dbba46383f1a33', runtime)
    process_report(base, 'iot-gpu-benchmark-29a', '9e1aa36696de44faaaeb635e96fa8cb6', result)
    assert 'GPU driver installation completed!' in (base / 'process/iot-gpu-driver-29c.journal.jsonl').read_text()
    assert 'ExecMainStatus=2\n' in (base / 'process/iot-gpu-driver-29b.unit.txt').read_text()
    assert "No such file or directory" in (base / 'process/iot-gpu-driver-29b.journal.jsonl').read_text()
    assert (base / 'runtime/native/ansible.exit').read_text().strip() == '0'
    assert 'unreachable=0    failed=0' in (base / 'runtime/native/ansible.stdout').read_text()
    assert 'ansible-core==2.21.5' in (base / 'runtime/native/freeze.stdout').read_text()
    assert '580.178.04, 16384 MiB' in (base / 'readback/gpu.csv').read_text()
    model = read(base, 'benchmark/native/model-lock.json'); lock = read(base, 'benchmark/native/runtime-lock.json')
    assert read(base, 'assets/native/model-files.json') == model['files']
    image = read(base, 'benchmark/native/image.json')
    assert lock['image'] in image['RepoDigests'] and image['Architecture'] == 'amd64'
    assert image['Config']['Labels']['org.opencontainers.image.revision'] == lock['llama_revision']
    reports = {}; batches = {}; identifiers = set(); commands = []
    for mode, layers in [('cpu', '0'), ('gpu', '99')]:
        batch = read(base, 'benchmark/native/' + mode + '-requests.json'); batches[mode] = batch
        assert len(batch['warmup']) == 3 and len(batch['measured']) == 9
        assert batch['parameters']['cache_prompt'] is False and batch['parameters']['max_tokens'] == 64
        assert batch['parameters']['seed'] == 7 and batch['parameters']['temperature'] == 0
        assert [(r['repeat'], r['prompt_index']) for r in batch['measured']] == [(r, p) for r in range(3) for p in range(3)]
        for row in batch['warmup'] + batch['measured']:
            assert row['status'] == 200 and row['text'] and 0 < row['ttft_seconds'] <= row['elapsed_seconds']
            assert all(math.isfinite(row[key]) for key in ['ttft_seconds', 'elapsed_seconds'])
            assert row['usage']['completion_tokens'] > 0 and row['usage']['prompt_tokens_details']['cached_tokens'] == 0
            assert [event['usage'] for event in row['events'] if event.get('usage')][-1] == row['usage']
            chunks = [choice.get('delta', {}).get('content', '') for event in row['events'] for choice in event.get('choices', [])]
            assert all(value is None or isinstance(value, str) for value in chunks)
            assert ''.join(value for value in chunks if value is not None) == row['text']
            assert {event['system_fingerprint'] for event in row['events']} == {'b11977-7ac5727d6'}
            ids = {event['id'] for event in row['events']}; assert len(ids) == 1 and not identifiers.intersection(ids)
            identifiers.update(ids)
        reports[mode] = {'requests': 9, 'median_ttft_seconds': median(r['ttft_seconds'] for r in batch['measured']),
            'median_elapsed_seconds': median(r['elapsed_seconds'] for r in batch['measured']),
            'reported_completion_tokens': sum(r['usage']['completion_tokens'] for r in batch['measured'])}
        assert reports[mode] == result[mode]
        container = read(base, 'benchmark/native/' + mode + '-inspect.json')
        assert container['Image'] == image['Id'] and container['Config']['Image'] == lock['image']
        assert container['Config']['User'] == '10001:10001' and container['HostConfig']['ReadonlyRootfs']
        assert container['HostConfig']['NanoCpus'] == 3_000_000_000 and container['HostConfig']['Memory'] == 10 * 1024**3
        assert container['HostConfig']['PortBindings']['8080/tcp'] == [{'HostIp': '127.0.0.1', 'HostPort': '18084'}]
        args = container['Args']; assert args[args.index('--n-gpu-layers') + 1] == layers
        normalized = args.copy(); normalized[normalized.index('--n-gpu-layers') + 1] = 'PROFILE'
        commands.append(normalized)
        exited({'State': read(base, 'benchmark/native/' + mode + '-stopped.json')})
    assert commands[0] == commands[1] and batches['cpu']['parameters'] == batches['gpu']['parameters']
    assert batches['cpu']['workload'] == batches['gpu']['workload'] and len(identifiers) == 24
    ratio = reports['cpu']['median_elapsed_seconds'] / reports['gpu']['median_elapsed_seconds']
    ttft = reports['cpu']['median_ttft_seconds'] / reports['gpu']['median_ttft_seconds']
    assert ratio == result['elapsed_speedup_ratio'] and ttft == result['ttft_speedup_ratio']
    equal = all(a['text'] == b['text'] for a, b in zip(batches['cpu']['measured'], batches['gpu']['measured']))
    assert equal == result['paired_texts_identical'] is False
    assert result['agent_route_changed'] is False and result['coursework_complete'] is False
    return {'passed': True, 'payloads': count, 'actual_model_bytes': assets['actual_model_bytes'], 'measured_requests_per_mode': 9,
        'cpu_median_seconds': reports['cpu']['median_elapsed_seconds'], 'gpu_median_seconds': reports['gpu']['median_elapsed_seconds'],
        'elapsed_speedup_ratio': ratio, 'ttft_speedup_ratio': ttft, 'paired_texts_identical': equal,
        'agent_route_changed': False, 'coursework_complete': False,
        'scope': 'Recorded single-host GPU-offload latency experiment; capped outputs differ. Not a routed agent, correctness or concurrency result.'}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
