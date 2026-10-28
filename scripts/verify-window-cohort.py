"""Independently compare native offline/online cohort captures with all retained Kafka/Flink windows and Docker receipts."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from evidence_integrity import ROOT, verified, source_bindings

FIELDS = ('reading_count', 'temperature_sum', 'temperature_mean', 'vibration_max')


def stamp(value):
    """Normalize original timezone-aware timestamps without changing their event-time meaning."""
    result = datetime.fromtimestamp(value, timezone.utc) if isinstance(value, (int, float)) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    assert result.tzinfo is not None
    return result


def identity(row):
    """Use machine and event time as the independent unique finalized-window key."""
    return row['machine_id'], stamp(row['event_timestamp'])


def check_container(receipt, image, online=False):
    """Check actual exit, isolation limits and immutable offline mounts for each native worker."""
    state = receipt['State']; host = receipt['HostConfig']
    assert state['ExitCode'] == 0 and not state['OOMKilled'] and not state['Running']
    assert receipt['Image'] == image and receipt['Config']['User'] == '10001:10001'
    assert host['Memory'] == host['MemorySwap'] == 4 * 1024**3
    assert host['NanoCpus'] == 2_000_000_000 and host['ReadonlyRootfs'] and host['CapDrop'] == ['ALL']
    assert host['NetworkMode'] == 'host' and 'no-new-privileges' in host['SecurityOpt']
    mounts = {m['destination']: m['rw'] for m in receipt['Mounts']}
    assert mounts['/src'] is False
    if online:
        assert mounts['/work/offline'] is False and mounts['/work/offline-reports'] is False


def main():
    """Verify original bytes, Git inputs, every offline value and all native online values/timestamps independently."""
    base = ROOT / 'docs/evidence/window-cohort-linux'; read = verified(base, 94, 90)
    source = read('run/source.json'); assert source_bindings(source) == 7 and source == read('source-after.json')
    assert source_bindings(read('run/online-inspection/source.json')) == 1
    parent_raw = (ROOT / 'docs/evidence/day3/stream-report.json').read_bytes()
    parent_hash = sha256(parent_raw).hexdigest(); assert parent_hash == source['files']['docs/evidence/day3/stream-report.json']
    parent = json.loads(parent_raw); generated = parent['generated']; windows = generated['windows']
    assert parent['passed'] and generated['runtime']['passed'] and len(windows) == 5760
    assert generated['durable_ledger_records'] == 57600 and generated['kafka_end_offset'] == 116928
    assert sum(r['reading_count'] for r in windows) == 57600 and len({identity(r) for r in windows}) == 5760
    ordered = sorted(windows, key=lambda r: (r['event_timestamp'], r['machine_id']))
    preflight = read('run/preflight.json'); budget = read('run/budget.json')
    assert budget['fits'] and budget['estimated_seconds'] + budget['reserve_seconds'] < budget['remaining_seconds']
    assert abs(sum(v['estimated_seconds_with_margin'] for v in budget['steps'].values()) - budget['estimated_seconds']) < 1e-9
    assert preflight['ram_available_bytes'] > 4 * 1024**3 and preflight['ssd_free_bytes'] > 1024**3
    results = {}; latest = {}
    for phase, count in [('pilot', 256), ('intermediate', 1024), ('full', 5760)]:
        selected = ordered[:count]; expected = {identity(r): r for r in selected}; latest = {}
        for row in selected:
            if row['machine_id'] not in latest or stamp(row['event_timestamp']) > stamp(latest[row['machine_id']]['event_timestamp']):
                latest[row['machine_id']] = row
        offline_hash = None
        for destination in ('offline', 'online'):
            for operation in ('write', 'replay'):
                name = f'{destination}-{operation}'; report = read(f'run/{phase}/{name}.json')
                binding = report['source_binding']; assert report['passed'] and report['destination'] == destination and report['operation'] == operation
                assert binding['parent_sha256'] == parent_hash and binding['selected_windows'] == count and binding['selected_readings'] == count * 10
                assert binding['full_windows'] == 5760 and binding['full_readings'] == 57600 and binding['selected_machines'] == 20
                mode = f'native_{destination}_only' if operation == 'write' else f'completed_{destination}_replay_noop'
                assert report['mode_counts'] == {mode: count}
                receipt = read(f'run/{phase}/{name}-container.json'); check_container(receipt, preflight['image_id'], destination == 'online')
                if destination == 'offline':
                    assert report['offline_rows'] == report['historical_rows'] == count and report['online_all_missing']
                    actual = {identity(r): r for r in report['rows']}; assert len(report['rows']) == len(actual) == count and actual.keys() == expected.keys()
                    for key, row in actual.items():
                        assert all(row[f] == expected[key][f] for f in FIELDS)
                        assert stamp(row['created']) == stamp(expected[key]['created'])
                    if offline_hash is None: offline_hash = report['offline_sha256']
                    assert report['offline_sha256'] == offline_hash
                else:
                    assert report['acknowledged_windows'] == count and report['machines'] == 20 and report['offline_sha256_unchanged'] == offline_hash
                    actual = {r['machine_id']: r for r in report['online_rows']}; assert len(report['online_rows']) == len(actual) == 20 and actual.keys() == latest.keys()
                    assert all(actual[m][f] == latest[m][f] for m in latest for f in FIELDS)
        timing = read(f'run/{phase}/verification.json'); assert timing['passed'] and timing['rows'] == count
        results[phase] = {'windows': count, 'four_containers_wall_seconds': sum(s['wall_seconds'] for s in timing['steps'].values())}
    native = read('run/online-inspection/native.json'); online = native['online_dict']
    assert native['passed'] and native['machines'] == 20 and set(native['fields']) == set(FIELDS)
    assert native['registry_sha256_before'] == native['registry_sha256_after'] == read('run/full/offline-replay.json')['registry_sha256']
    for i, machine in enumerate(online['machine_id']):
        assert machine in latest
        for field in FIELDS:
            assert online[field][i] == latest[machine][field] and stamp(online[field + '__ts'][i]) == stamp(latest[machine]['event_timestamp'])
    assert len(online['machine_id']) == len(set(online['machine_id'])) == 20
    proto = native['online_proto']; vectors = dict(zip(proto['metadata']['feature_names']['val'], proto['results'], strict=True))
    for field in FIELDS:
        assert vectors[field]['statuses'] == ['PRESENT'] * 20
        assert len(vectors[field]['event_timestamps']) == 20
        for i, machine in enumerate(online['machine_id']):
            assert stamp(vectors[field]['event_timestamps'][i]) == stamp(latest[machine]['event_timestamp'])
    inspection = read('run/online-inspection/container.json'); assert inspection['State']['ExitCode'] == 0 and not inspection['State']['OOMKilled']
    assert inspection['HostConfig']['ReadonlyRootfs'] and inspection['HostConfig']['Memory'] == 1024**3
    assert all(not m['rw'] for m in inspection['Mounts'] if m['destination'] != '/out')
    reviews = [json.loads(p.read_bytes()) for p in (base / 'run/review').glob('*.json')]
    assert any(r['jobs'][0]['status'] == 'running' for r in reviews) and any(r['jobs'][1]['status'] == 'running' for r in reviews)
    assert any(all(j['status'] == 'exited' and j['write']['source_windows'] == j['replay']['source_windows'] == 5760 for j in r['jobs']) for r in reviews)
    assert read('run/verification.json')['full_completed']
    for history in ('rounding-failure', 'project-collision'):
        assert source_bindings(read(f'history/{history}/source.json')) == 7
    assert read('history/state-readback.json') == {'rounding-failure': {'offline': {'intents': 256, 'acknowledged': 256}, 'online': {'intents': 256, 'acknowledged': 256}}, 'project-collision': {'offline': {'intents': 256, 'acknowledged': 256}}}
    assert b'Latest native vectors differ' in (base / 'history/rounding-failure/pilot/online-write.stderr').read_bytes()
    assert b'Offline stage populated the fresh online project' in (base / 'history/project-collision/pilot/offline-write.stderr').read_bytes()
    for entry in read('log-inventory.json'):
        assert entry['bytes'] > 65536 and len(entry['sha256']) == 64
        assert len((base / entry['head']).read_bytes()) == len((base / entry['tail']).read_bytes()) == entry['excerpt_bytes'] == 8192
    print(json.dumps({'passed': True, 'payload_files': 94, 'native_files': 90, 'git_inputs': 8, 'results': results, 'online_values': 80, 'native_event_timestamps': 80, 'scope': 'Retained complete Kafka/Flink cohort, native separate stores; Airflow CI has its own smaller run proof.'}, indent=2))


if __name__ == '__main__':
    main()
