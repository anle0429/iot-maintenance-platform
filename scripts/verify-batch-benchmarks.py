"""Verify recorded batch benchmarks and independently reconstruct every small-source full-result checksum without Spark."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import gzip
import json
import math
from pathlib import Path, PurePosixPath
import re
import statistics
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'docs/evidence/batch-benchmarks'
sys.path.insert(0, str(ROOT))
FIELDS = ['event_id', 'machine_id', 'operating_status', 'event_ts', 'received_ts', 'created_ts',
          'temperature_c', 'vibration_rms_mm_s', 'current_a', 'rpm', 'pressure_bar', 'schema_version']
TIMESTAMPS = {'event_ts', 'received_ts', 'created_ts'}
SENSORS = {'temperature_c', 'vibration_rms_mm_s', 'current_a', 'rpm', 'pressure_bar'}
TASKS = ['preflight', 'schema_dedup_pairs', 'validate_corrections', 'skew_pairs', 'validate_skew']
RUNTIME = {
    'airflow/dags/iot_data_benchmarks.py', 'pipelines/__init__.py', 'pipelines/benchmark_airflow.py',
    'pipelines/data_corrections_benchmark.py', 'pipelines/skew_job.py', 'pipelines/skew_metrics.py',
    'pipelines/orchestration_contract.py', 'pipelines/factory_policy.py', 'pipelines/transforms.py',
    'pipelines/configs/factory-risk-policies.json', 'generator/__init__.py', 'generator/storage.py',
    'generator/config.py', 'generator/simulation.py', 'scripts/run-data-benchmarks.py', 'infra/spark/Dockerfile'}
TEST_SOURCES = {'tests/test_benchmark_airflow.py', 'tests/test_data_corrections_benchmark.py'}


def load(path):
    """Decode one explicitly named structured receipt without touching service state."""
    return json.loads(path.read_bytes())


def independent_row(value, source_path, source_line):
    """Normalize original JSON independently, including UTC microseconds and Spark's declared double values."""
    assert type(value['schema_version']) is int and value['schema_version'] in (1, 2)
    expected = set(FIELDS) - ({'pressure_bar'} if value['schema_version'] == 1 else set())
    assert set(value) == expected
    result = {}
    for name in FIELDS:
        item = value.get(name)
        if name in TIMESTAMPS:
            stamp = datetime.fromisoformat(item.replace('Z', '+00:00'))
            assert stamp.tzinfo is not None
            result[name] = stamp.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        elif name in SENSORS:
            if name == 'pressure_bar' and value['schema_version'] == 1:
                assert item is None
                result[name] = None
            else:
                assert type(item) in (int, float) and math.isfinite(item)
                result[name] = float(item)
        else:
            result[name] = item
    assert result['event_ts'] <= result['received_ts'] <= result['created_ts']
    result['source_path'], result['source_line'] = source_path, source_line
    return result


def independent_source(source):
    """Authenticate all original telemetry bytes and select winners with an independent Python ordering oracle."""
    manifest = load(source / 'manifest.json')
    files = sorted((row for row in manifest['files'] if row['path'].startswith('telemetry/')), key=lambda row: row['path'])
    assert len(files) == 2 and sum(row['rows'] for row in files) <= 100_000
    rows, winners, immutable = [], {}, {}
    for item in files:
        path = source / item['path']
        assert not path.is_symlink() and path.resolve().is_relative_to(source.resolve())
        body = path.read_bytes()
        assert len(body) == item['bytes'] and sha256(body).hexdigest() == item['sha256']
        lines = body.splitlines()
        assert len(lines) == item['rows']
        for line_number, line in enumerate(lines, start=1):
            row = independent_row(json.loads(line), item['path'], line_number)
            identity = row['event_id']
            payload = tuple(row[key] for key in FIELDS if key != 'created_ts')
            assert identity not in immutable or immutable[identity] == payload, 'Conflicting source reading'
            immutable[identity] = payload
            previous = winners.get(identity)
            if (previous is None or row['created_ts'] > previous['created_ts'] or
                    (row['created_ts'] == previous['created_ts'] and
                     (row['source_path'], -row['source_line']) < (previous['source_path'], -previous['source_line']))):
                winners[identity] = row
            rows.append(row)
    return manifest, files, rows, list(winners.values())


def independent_summary(rows, metadata=False):
    """Reproduce Spark's row JSON hashes and four unsigned 64-bit sums, failing on any serialization mismatch."""
    columns = FIELDS + (['source_path', 'source_line'] if metadata else [])
    total, minimum, maximum, sums = 0, None, None, [0, 0, 0, 0]
    for row in rows:
        serialized = json.dumps({key: row[key] for key in columns}, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        row_hash = sha256(serialized.encode('utf-8')).hexdigest()
        total += 1
        minimum = row_hash if minimum is None else min(minimum, row_hash)
        maximum = row_hash if maximum is None else max(maximum, row_hash)
        for part in range(4):
            sums[part] += int(row_hash[part * 16:(part + 1) * 16], 16)
    result = {'rows': total, 'minimum_row_sha256': minimum, 'maximum_row_sha256': maximum,
              **{'sum_' + str(index): str(value) for index, value in enumerate(sums)}}
    result['summary_sha256'] = sha256(json.dumps(result, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return result


def check_correction_oracle(source, report):
    """Check all measured and warmup summaries against independently read source rows, not a report's pass flag."""
    manifest, files, rows, winners = independent_source(source)
    summaries = {'schema': independent_summary(rows), 'dedup': independent_summary(winners, metadata=True)}
    assert report['input']['manifest_sha256'] == sha256((source / 'manifest.json').read_bytes()).hexdigest()
    assert report['input']['files'] == files
    assert (len(rows), len(winners), len(rows) - len(winners)) == (58_752, 57_600, 1_152)
    assert report['input']['rows'] == len(rows) and report['input']['unique_events'] == len(winners)
    for measurement in report['measurements'] + report['warmups']:
        assert measurement['summary'] == summaries[measurement['experiment']], 'Java/Python JSON or full-result checksum mismatch'
        if 'result_sha256' in measurement:
            assert measurement['result_sha256'] == summaries[measurement['experiment']]['summary_sha256']
    assert report['legacy_v1_only_failure'] == {'pressure_column_missing': True, 'rows': len(rows),
        'timed_as_equivalent': False, 'v2_exports_with_lost_pressure': sum(row['schema_version'] == 2 for row in rows)}
    return summaries


def archive(path):
    """Read only bounded regular archive members; never extract paths or follow archive links."""
    result, total = {}, 0
    with tarfile.open(path) as bundle:
        for index, member in enumerate(bundle):
            assert index < 10_000
            name = PurePosixPath(member.name)
            assert not name.is_absolute() and '..' not in name.parts and '\\' not in member.name
            assert member.isdir() or member.isfile()
            if member.isfile():
                assert 0 <= member.size <= 5_000_000 and member.name not in result
                total += member.size
                assert total <= 100_000_000
                result[member.name] = bundle.extractfile(member).read()
                assert len(result[member.name]) == member.size
    return result


def authenticate_capsule():
    """Authenticate the complete closed payload inventory before interpreting native receipts."""
    manifest = load(EVIDENCE / 'manifest.json')
    expected = {row['path'] for row in manifest['files']}
    assert len(expected) == len(manifest['files'])
    actual = {p.relative_to(EVIDENCE).as_posix() for p in EVIDENCE.rglob('*') if p.is_file()} - {'manifest.json'}
    assert actual == expected
    for item in manifest['files']:
        relative = PurePosixPath(item['path'])
        assert not relative.is_absolute() and '..' not in relative.parts
        path = EVIDENCE / item['path']
        assert not path.is_symlink() and path.resolve().is_relative_to(EVIDENCE.resolve())
        body = path.read_bytes()
        assert len(body) == item['bytes'] and sha256(body).hexdigest() == item['sha256'], item['path']
    return expected


def native_json(native, name):
    """Decode exactly one previously authenticated original JSON member."""
    return json.loads(native[name])


def check_packet(spec, packet_name, *, current=False):
    """Bind every loaded project module/test to the archived source packet and, for the final release, current code."""
    packet = archive(EVIDENCE / packet_name)
    source = spec['source_packet']
    assert source['base_commit'] == '0fa85c13df2ed5d9df7d5927316dcf4656af661d'
    records = {row['path']: row for row in source['files']}
    assert set(records) == set(packet) == RUNTIME | TEST_SOURCES
    runtime = {row['path']: row for row in spec['runtime_files']}
    assert set(runtime) == RUNTIME
    for name, item in records.items():
        assert len(packet[name]) == item['bytes'] and sha256(packet[name]).hexdigest() == item['sha256']
        if name in runtime:
            assert runtime[name] == item
        if current:
            body = (ROOT / name).read_bytes()
            assert body == packet[name] or body.replace(b'\r\n', b'\n') == packet[name], name
    return packet


def log_events(body):
    """Read original plain or structured Airflow log messages without executing their contents."""
    result = []
    for line in body.decode('utf-8').splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            result.append(line)
            continue
        if isinstance(item, dict):
            result.extend(value for key, value in item.items() if key in ('event', 'message', 'msg') and isinstance(value, str))
            for detail in item.get('error_detail', []):
                result.append(str(detail['exc_type']) + ': ' + str(detail['exc_value']))
                result.extend(frame['name'] + ' ' + frame['filename'] for frame in detail.get('frames', []))
        elif isinstance(item, str):
            result.append(item)
    return result


def check_airflow(native, run_id, *, successful):
    """Require real final API states and task identities, keeping failed predecessors distinct."""
    prefix = 'outputs/data-benchmarks/' + run_id + '/'
    snapshot = native_json(native, prefix + 'airflow-final.json')
    run, tasks = snapshot['run'], snapshot['tasks']['task_instances']
    assert run['dag_id'] == 'iot_data_benchmarks' and run['dag_run_id'] == run_id
    assert run['conf'] == {'benchmark_id': run_id}
    assert run['state'] == ('success' if successful else 'failed')
    assert len(tasks) == 5 and {task['task_id'] for task in tasks} == set(TASKS)
    indexed = {task['task_id']: task for task in tasks}
    for task in tasks:
        assert task['dag_id'] == run['dag_id'] and task['dag_run_id'] == run_id
        if successful:
            assert task['state'] == 'success' and task['pool'] == 'spark_jobs'
            assert task['pool_slots'] == 1 and task['try_number'] == 1
            assert datetime.fromisoformat(task['start_date'].replace('Z', '+00:00')) <= datetime.fromisoformat(task['end_date'].replace('Z', '+00:00'))
    if successful:
        for previous, following in zip(TASKS, TASKS[1:]):
            assert datetime.fromisoformat(indexed[previous]['end_date'].replace('Z', '+00:00')) <= datetime.fromisoformat(indexed[following]['start_date'].replace('Z', '+00:00'))
    return snapshot


def task_log(native, run_id, task):
    """Select the actual first-attempt log for one named native task."""
    path = f'data/runtime/airflow-logs/dag_id=iot_data_benchmarks/run_id={run_id}/task_id={task}/attempt=1.log'
    return log_events(native[path])


def check_runtime(runtime, phase, *, metrics=False):
    """Check real container exits, image identity and captured resource/isolation attributes."""
    assert runtime['exit_code'] == 0 and runtime['oom_killed'] is False
    assert re.fullmatch(r'[a-f0-9]{64}', runtime['container_id'])
    assert re.fullmatch(r'sha256:[a-f0-9]{64}', runtime['image_id'])
    assert runtime['image'] == 'iot-coursework-spark:3.5.9'
    assert runtime['network_mode'] == 'none' and runtime['read_only_rootfs'] is True
    cores, gib = (1, 1) if metrics else (4, 10) if phase == 'corrections' else (6, 28)
    assert runtime['memory_limit_bytes'] == gib * 1024**3 and runtime['nano_cpus'] == cores * 1_000_000_000
    assert runtime['elapsed_seconds'] > 0 and runtime['resources']
    for sample in runtime['resources']:
        assert sample['available_ram_bytes'] >= 4 * 1024**3
        assert sample['free_bytes']['/srv/iot-data'] >= 285_000_000_000
        assert sample['free_bytes']['/srv/iot-fast'] >= 35_000_000_000
        assert sample['measurement_context'] == 'Read-only host bind-mount probe'
    if metrics:
        assert runtime['entrypoint'] == 'python3' and runtime['command'][:2] == ['-m', 'pipelines.skew_metrics']
    else:
        command = runtime['command']
        assert command[command.index('--master') + 1] == f'local[{cores}]'
        assert command[command.index('--driver-memory') + 1] == ('7g' if phase == 'corrections' else '20g')
        assert 'spark.driver.extraJavaOptions=-Djava.io.tmpdir=/benchmark-scratch' in command


def check_grid(report, phase, repetitions):
    """Recompute the full strategy/repetition grid and every median, without requiring a favorable speed ratio."""
    methods = [('schema', 'inferred'), ('schema', 'declared'), ('dedup', 'window'), ('dedup', 'aggregate')] if phase == 'corrections' else [('skew', 'baseline'), ('skew', 'broadcast')]
    wanted = {(experiment, strategy, repeat) for experiment, strategy in methods for repeat in range(repetitions)}
    measured = report['measurements']
    actual = [(row.get('experiment', 'skew'), row['strategy'], row['repeat']) for row in measured]
    assert len(actual) == len(set(actual)) == len(wanted) and set(actual) == wanted
    assert len({row['group'] for row in measured}) == len(measured)
    assert report['passed'] and report['repetitions'] == repetitions and report['spark_version'] == '3.5.9'
    assert report['spark_conf']['spark.sql.adaptive.enabled'] == 'false'
    assert report['spark_conf']['spark.sql.autoBroadcastJoinThreshold'] == '-1'
    assert report['spark_conf']['spark.sql.shuffle.partitions'] == ('8' if phase == 'corrections' else '32')
    for row in measured:
        assert type(row['seconds']) in (int, float) and math.isfinite(row['seconds']) and row['seconds'] > 0
        assert re.fullmatch(r'[a-f0-9]{64}', row['result_sha256'])
        if phase == 'corrections':
            assert row['group'] == f"corrections-{row['experiment']}-{row['strategy']}-{row['repeat']}"
            assert row['exact_multiset_matches_python_oracle'] is True
            assert row['result_rows'] == (58_752 if row['experiment'] == 'schema' else 57_600)
            if row['experiment'] == 'dedup':
                assert ('Window' if row['strategy'] == 'window' else 'min_by') in row['physical_plan']
            else:
                assert 'FileScan json' in row['physical_plan']
        else:
            assert row['group'] == f"skew-{row['strategy']}-{row['repeat']}"
            assert ('SortMergeJoin' if row['strategy'] == 'baseline' else 'BroadcastHashJoin') in row['physical_plan']
    if phase == 'corrections':
        for experiment, strategy in methods:
            values = [row['seconds'] for row in measured if row['experiment'] == experiment and row['strategy'] == strategy]
            assert report['median_seconds'][experiment][strategy] == statistics.median(values)
        for experiment, before, after in [('schema', 'inferred', 'declared'), ('dedup', 'window', 'aggregate')]:
            assert report['observed_baseline_over_candidate_ratio'][experiment] == report['median_seconds'][experiment][before] / report['median_seconds'][experiment][after]
        assert {(row['experiment'], row['strategy']) for row in report['warmups']} == set(methods) and len(report['warmups']) == 4
    else:
        for _, strategy in methods:
            assert report['median_seconds'][strategy] == statistics.median(row['seconds'] for row in measured if row['strategy'] == strategy)
        assert report['observed_speedup'] == report['median_seconds']['baseline'] / report['median_seconds']['broadcast']
    return measured


def check_skew_values(native, run_id, report):
    """Authenticate every complete 4,320-row aggregate result and preserve exact equality across paired strategies."""
    reference = None
    for row in report['measurements']:
        name = f"data/benchmarks/{run_id}/skew/{row['group']}.json"
        values = native_json(native, name)
        assert len(values) == row['result_rows'] == 4_320
        assert values == sorted(values, key=lambda value: (value['factory_id'], value['event_hour']))
        digest = sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        assert digest == row['result_sha256'] == report['result_sha256']
        assert digest == '7709e19ad539bcff7eeab3c17925c9a18d406dd9f9b69af0c01822d9ca41f877'
        assert sum(value['reading_count'] for value in values) == 17_280_000
        counts = {factory: sum(value['reading_count'] for value in values if value['factory_id'] == factory) for factory in ['F-01', 'F-02', 'F-03']}
        assert counts == {'F-01': 12_096_000, 'F-02': 2_592_000, 'F-03': 2_592_000}
        if reference is None:
            reference = values
        assert values == reference


def check_successful_run(native, run_id, packet_name, repetitions, source, *, current=False):
    """Bind native five-task success to source, exact reports, real container returns and completed metrics."""
    prefix = 'outputs/data-benchmarks/' + run_id + '/'
    spec = native_json(native, prefix + 'spec.json')
    assert spec['run_id'] == run_id and spec['repetitions'] == repetitions
    check_packet(spec, packet_name, current=current)
    admitted = native_json(native, prefix + 'preflight.json')
    assert admitted['passed'] and admitted['spec_sha256'] == sha256(native[prefix + 'spec.json']).hexdigest()
    assert admitted['runtime_files'] == 16 and admitted['input_files'] == len(spec['input_files'])
    bound = {row['path']: row for row in spec['input_files']}
    binding_name = prefix + 'skew-binding.json'
    assert binding_name in bound and bound[binding_name]['sha256'] == sha256(native[binding_name]).hexdigest()
    for item in spec['input_files']:
        if item['path'] in native:
            assert len(native[item['path']]) == item['bytes'] and sha256(native[item['path']]).hexdigest() == item['sha256']
    snapshot = check_airflow(native, run_id, successful=True)
    start = datetime.fromisoformat(spec['session']['started_utc'].replace('Z', '+00:00'))
    from datetime import timedelta
    assert spec['session']['name'] == 'iot-cw-vm' and spec['session']['machine_type'] == 'e2-highmem-8'
    assert spec['session']['max_seconds'] == 28_800 and spec['session']['termination_action'] == 'STOP'
    assert start <= datetime.fromisoformat(snapshot['run']['start_date'].replace('Z', '+00:00'))
    assert datetime.fromisoformat(snapshot['run']['end_date'].replace('Z', '+00:00')) < start + timedelta(hours=8)
    returned = task_log(native, run_id, 'preflight')
    assert any('Returned value was:' in line and admitted['spec_sha256'] in line for line in returned)
    reports = {}
    images, containers = set(), set()
    for phase, measure_task, gate_task in [('corrections', 'schema_dedup_pairs', 'validate_corrections'), ('skew', 'skew_pairs', 'validate_skew')]:
        base = prefix + phase + '/'
        report = native_json(native, base + 'benchmark.json')
        runtime = native_json(native, base + 'runtime.json')
        metric_runtime = native_json(native, base + 'metrics-runtime.json')
        check_runtime(runtime, phase)
        check_runtime(metric_runtime, phase, metrics=True)
        images.update([runtime['image_id'], metric_runtime['image_id']])
        containers.update([runtime['container_id'], metric_runtime['container_id']])
        app = report['application_id']
        assert re.fullmatch(r'local-[0-9]+', app)
        assert any('Returned value was:' in line and runtime['container_id'] in line and app in line
                   for line in task_log(native, run_id, measure_task)), measure_task
        assert any('Returned value was:' in line and "'passed': True" in line and app in line
                   for line in task_log(native, run_id, gate_task)), gate_task
        marker = native_json(native, f'data/benchmarks/{run_id}/{phase}/_RUN_SUCCESS.json')
        assert marker['report_sha256'] == sha256(native[base + 'benchmark.json']).hexdigest()
        verification = native_json(native, base + 'verification.json')
        metrics = native_json(native, base + 'task-metrics.json')
        assert verification['passed'] and verification['input_and_code_unchanged']
        assert verification['application_id'] == app
        assert verification['benchmark_sha256'] == sha256(native[base + 'benchmark.json']).hexdigest()
        assert verification['task_metrics_sha256'] == sha256(native[base + 'task-metrics.json']).hexdigest()
        measured = check_grid(report, phase, repetitions)
        assert verification['measurements'] == len(measured) and metrics['application_ended']
        for item in measured:
            totals = metrics['groups'][item['group']]['totals']
            assert totals['task_attempts'] > 0 and totals['failed_attempts'] == 0
        if phase == 'corrections':
            check_correction_oracle(source, report)
            assert report['input_unchanged']
        else:
            check_skew_values(native, run_id, report)
            assert report['binding'] == native_json(native, binding_name)
            assert report['binding']['unique_rows'] == 17_280_000
            assert report['max_partition_share'] >= 0.7
        reports[phase] = {'report': report, 'verification': verification, 'metrics': metrics}
    assert len(images) == 1 and len(containers) == 4
    return reports


def check_eventlogs(collection, reports, temporary):
    """Stream lossless gzip logs into bounded local files and recompute native task metrics independently of receipts."""
    from pipelines.skew_metrics import summarize
    assert collection['passed'] and {row['phase'] for row in collection['events']} == {'corrections', 'skew'}
    assert len(collection['events']) == 2
    result = {}
    for item in collection['events']:
        phase = item['phase']
        app = item['application_id']
        report, verification = reports[phase]['report'], reports[phase]['verification']
        assert app == report['application_id'] and re.fullmatch(r'local-[0-9]+', app)
        compressed = EVIDENCE / 'eventlogs' / (app + '.jsonl.gz')
        body = compressed.read_bytes()
        assert len(body) == item['gzip_bytes'] and sha256(body).hexdigest() == item['gzip_sha256']
        path = temporary / (app + '.jsonl')
        assert path.resolve().is_relative_to(temporary.resolve())
        digest, size = sha256(), 0
        with gzip.open(compressed, 'rb') as original, path.open('xb') as destination:
            while block := original.read(1024 * 1024):
                size += len(block)
                assert size <= 300_000_000
                digest.update(block)
                destination.write(block)
        assert size == item['bytes'] == verification['event_log_bytes']
        assert digest.hexdigest() == item['sha256'] == verification['event_log_sha256']
        recomputed = summarize(path)
        assert recomputed == reports[phase]['metrics']
        result[phase] = {'application_id': app, 'raw_bytes': size, 'groups_recomputed': len(recomputed['groups'])}
    return result


def check():
    """Verify the complete recorded capsule without Docker, network, VM access or a fresh Spark execution."""
    payloads = authenticate_capsule()
    review = load(EVIDENCE / 'review.json')
    final_run, pilot = review['final_run'], review['successful_pilot_run']
    assert final_run == 'bench-paired-20261101a' and pilot == 'bench-pilot-20261101d'
    assert review['final_source_packet'] == 'source-d.tar.gz'
    assert set(review['failed_runs']) == {'bench-pilot-20261101b', 'bench-pilot-20261101c'}
    native = archive(EVIDENCE / 'native.tar.gz')
    runs = {final_run, pilot, *review['failed_runs']}
    for name in native:
        assert any(name.startswith('outputs/data-benchmarks/' + run + '/') or
                   (name.startswith('data/benchmarks/' + run + '/') and name.endswith('.json')) or
                   name.startswith('data/runtime/airflow-logs/dag_id=iot_data_benchmarks/run_id=' + run + '/') for run in runs)
    collection = load(EVIDENCE / 'collection.json')
    assert set(collection['runs']) == runs and collection['native_files'] == len(native)
    # Authenticate the generator code before importing it for bounded regeneration.
    check_packet(native_json(native, 'outputs/data-benchmarks/' + final_run + '/spec.json'),
                 review['final_source_packet'], current=True)
    output_root = ROOT / 'outputs'
    output_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='batch-benchmark-check-', dir=output_root) as directory:
        temporary = Path(directory)
        assert temporary.resolve().is_relative_to(output_root.resolve())
        from generator.config import Config
        from generator.simulation import generate
        from generator.storage import write_dataset
        original = load(EVIDENCE / 'small-source-manifest.json')
        config = Config(**original['config'])
        assert config.machine_count == 20 and config.duration_hours == 48 and config.unique_rows == 57_600
        source = temporary / 'source'
        regenerated = write_dataset(generate(config), config, source)
        assert regenerated == original
        assert (source / 'manifest.json').read_bytes() == (EVIDENCE / 'small-source-manifest.json').read_bytes()
        final = check_successful_run(native, final_run, review['final_source_packet'], 3, source, current=True)
        check_successful_run(native, pilot, review['final_source_packet'], 1, source, current=True)
        for run_id, packet_name in [('bench-pilot-20261101b', 'source-b.tar.gz'), ('bench-pilot-20261101c', 'source-c.tar.gz')]:
            prefix = 'outputs/data-benchmarks/' + run_id + '/'
            packet = check_packet(native_json(native, prefix + 'spec.json'), packet_name)
            state = check_airflow(native, run_id, successful=False)
            states = {row['task_id']: row['state'] for row in state['tasks']['task_instances']}
            if run_id.endswith('b'):
                assert states['preflight'] == 'failed' and states['schema_dedup_pairs'] == 'upstream_failed'
                messages = task_log(native, run_id, 'preflight')
                assert any('TypeError' in line and 'JSON object' in line and 'NoneType' in line for line in messages)
                assert any('resource_sample' in line for line in messages)
                assert b'log_config=' not in packet['pipelines/benchmark_airflow.py']
                assert b"LogConfig(type='json-file'" in archive(EVIDENCE / 'source-c.tar.gz')['pipelines/benchmark_airflow.py']
            else:
                assert states['validate_corrections'] == 'success' and states['skew_pairs'] == 'failed' and states['validate_skew'] == 'upstream_failed'
                failed_runtime = native_json(native, prefix + 'skew/runtime.json')
                assert failed_runtime['exit_code'] != 0
                assert b'snappy' in native[prefix + 'skew/console.txt'].lower()
                assert b'failed to map segment' in native[prefix + 'skew/console.txt'].lower()
                check_correction_oracle(source, native_json(native, prefix + 'corrections/benchmark.json'))
        events = check_eventlogs(collection, final, temporary)
    return {'passed': True, 'capsule_payloads': len(payloads), 'native_payloads': len(native),
            'source_runtime_files': 16, 'source_test_files': 2, 'successful_airflow_runs': 2,
            'successful_tasks_per_run': 5, 'failed_runs_preserved': 2, 'final_measured_queries': 18,
            'independent_schema_rows': 58_752, 'independent_dedup_rows': 57_600,
            'full_small_source_checksums_reproduced': True, 'complete_final_eventlogs_recomputed': events,
            'scope': 'Recorded native five-task Airflow experiments and immutable source/result integrity. Small source is regenerated and every schema/dedup checksum independently reproduced. Skew aggregate equality and task metrics are checked from original outputs; retained 17.28M Parquet is not bundled or rescanned here. No current service readiness, required speedup, production DP2 promotion, full100GB benchmark or rubric grade.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--native-report', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    assert bool(args.source) == bool(args.native_report), 'Supply source and native-report together'
    if args.output:
        args.output = args.output.resolve()
        if not args.output.is_relative_to(ROOT / 'outputs') or args.output.exists():
            raise ValueError('Use a fresh JSON receipt under outputs/')
    result = ({'passed': True, 'independent_summaries': check_correction_oracle(args.source, load(args.native_report))}
              if args.source else check())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
