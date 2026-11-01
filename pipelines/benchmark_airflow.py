"""Run source-bound correction and skew experiments as serial, resource-limited Airflow tasks."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import time

from pipelines.orchestration_contract import digest, save, utc_now


def run_directory(root: Path, run_id: str) -> Path:
    """Resolve only a new named benchmark under the project, rejecting traversal and linked parents."""
    if not isinstance(run_id, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', run_id):
        raise ValueError('Use a simple benchmark ID')
    target = root / 'outputs/data-benchmarks' / run_id
    if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
        raise ValueError('Benchmark path escapes the project')
    return target


def read_spec(root: Path, run_id: str, *, require_admission=True) -> tuple[Path, dict]:
    """Require a prepared source manifest and an actual bounded CPU-session receipt before every task."""
    target = run_directory(root, run_id)
    spec = json.loads((target / 'spec.json').read_text())
    if spec['run_id'] != run_id or spec['repetitions'] not in (1, 3):
        raise ValueError('Prepared benchmark identity differs')
    session = spec['session']
    if (session['name'] != 'iot-cw-vm' or session['machine_type'] != 'e2-highmem-8'
            or session['max_seconds'] != 28800 or session['termination_action'] != 'STOP'):
        raise ValueError('A bounded approved CPU session is required')
    deadline = datetime.fromisoformat(session['started_utc']) + timedelta(seconds=28800)
    if datetime.now(timezone.utc) + timedelta(minutes=15) >= deadline:
        raise ValueError('Keep fifteen minutes of session time for finalization')
    if require_admission and not (target / 'preflight.json').exists():
        raise ValueError('Airflow admission must pass before measurement or validation')
    if (target / 'preflight.json').exists():
        admitted = json.loads((target / 'preflight.json').read_text())
        if not admitted['passed'] or admitted['spec_sha256'] != digest(target / 'spec.json'):
            raise ValueError('Prepared specification changed after Airflow admission')
    return target, spec


def check_sources(root: Path, spec: dict) -> None:
    """Hash actual source, policy, JSONL and Parquet inputs; never trust an output pass flag alone."""
    for item in spec['runtime_files'] + spec['input_files']:
        relative = Path(item['path'])
        path = root / relative
        if relative.is_absolute() or '..' in relative.parts or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Unsafe source path')
        if path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
            raise ValueError('Source changed: ' + item['path'])


def resource_sample(required_ram: int = 4 * 1024**3) -> dict:
    """Measure host-visible Linux reserves without dumping environment or authentication data."""
    if all(Path(p).is_mount() for p in ['/srv/iot-data', '/srv/iot-fast']):
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        result = {'available_ram_bytes': int(memory['MemAvailable'].split()[0]) * 1024,
                  'free_bytes': {p: shutil.disk_usage(p).free for p in ['/srv/iot-data', '/srv/iot-fast']},
                  'measurement_context': 'Host mount roots'}
    else:
        # Airflow mounts the project, not both host disk roots. Its overlay filesystem
        # must never be reported as the VM data disk. Probe real host bind mounts.
        import docker
        code = ("import json,shutil;from pathlib import Path;"
                "m=dict(x.split(':',1) for x in Path('/proc/meminfo').read_text().splitlines());"
                "print(json.dumps({'available_ram_bytes':int(m['MemAvailable'].split()[0])*1024,"
                "'free_bytes':{'/srv/iot-data':shutil.disk_usage('/probe/data').free,"
                "'/srv/iot-fast':shutil.disk_usage('/probe/fast').free},"
                "'measurement_context':'Read-only host bind-mount probe'}))")
        client = docker.from_env()
        try:
            body = client.containers.run('iot-coursework-spark:3.5.9', ['-c', code], entrypoint='python3',
                                         remove=True, network_mode='none', read_only=True, cap_drop=['ALL'],
                                         log_config=docker.types.LogConfig(type='json-file', config={'max-size': '1m', 'max-file': '1'}),
                                         security_opt=['no-new-privileges:true'], mem_limit='128m', nano_cpus=100_000_000,
                                         volumes={'/srv/iot-data': {'bind': '/probe/data', 'mode': 'ro'},
                                                  '/srv/iot-fast': {'bind': '/probe/fast', 'mode': 'ro'}},
                                         labels={'iot.component': 'benchmark-resource-probe'})
            result = json.loads(body)
        finally:
            client.close()
    result['checked_utc'] = utc_now()
    if (result['available_ram_bytes'] < required_ram or result['free_bytes']['/srv/iot-data'] < 285_000_000_000
            or result['free_bytes']['/srv/iot-fast'] < 35_000_000_000):
        raise ValueError('Insufficient benchmark RAM or disk reserve')
    return result


def preflight(run_id: str) -> dict:
    """Admit the exact prepared run before scheduling either heavy Spark experiment."""
    root = Path(os.environ['IOT_ROOT']).resolve()
    target, spec = read_spec(root, run_id, require_admission=False)
    check_sources(root, spec)
    result = {'passed': True, 'checked_utc': utc_now(), 'resources': resource_sample(36 * 1024**3),
              'spec_sha256': digest(target / 'spec.json'), 'runtime_files': len(spec['runtime_files']),
              'input_files': len(spec['input_files']), 'scope': 'Prepared source identity and host reserves; no Spark task has run yet.'}
    save(target / 'preflight.json', result)
    return result


def execute_container(root: Path, target: Path, spec: dict, phase: str, command: list[str],
                      *, memory: str, cpus: int, entrypoint=None) -> dict:
    """Bound one sibling container, retain its real logs/exit and stop only that container on failure."""
    import docker
    result_dir = target / phase
    result_dir.mkdir(exist_ok=True)
    receipt_path = result_dir / ('runtime.json' if entrypoint is None else 'metrics-runtime.json')
    if receipt_path.exists():
        raise ValueError('Run receipt already exists; use another benchmark ID')
    image = 'iot-coursework-spark:3.5.9'
    args = {'detach': True, 'working_dir': str(root), 'network_mode': 'none', 'read_only': True,
            'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'], 'group_add': ['0'],
            'mem_limit': memory, 'nano_cpus': cpus * 1_000_000_000,
            'environment': {'PYTHONPATH': str(root), 'PYSPARK_PYTHON': 'python3', 'SPARK_LOCAL_IP': '127.0.0.1'},
            'volumes': {str(root): {'bind': str(root), 'mode': 'ro'},
                        str(target): {'bind': str(target), 'mode': 'rw'},
                        str(root / 'data/benchmarks' / spec['run_id']): {'bind': str(root / 'data/benchmarks' / spec['run_id']), 'mode': 'rw'},
                        str(root / 'data/benchmark-events' / spec['run_id']): {'bind': str(root / 'data/benchmark-events' / spec['run_id']), 'mode': 'rw'},
                        str(root / 'data/benchmark-scratch' / spec['run_id']): {'bind': '/benchmark-scratch', 'mode': 'rw'}},
            'tmpfs': {'/tmp': 'rw,nosuid,noexec,size=1073741824'},
            'labels': {'iot.benchmark': spec['run_id'], 'iot.phase': phase}}
    if entrypoint is not None:
        args['entrypoint'] = entrypoint
    minimum_ram = (28 if memory == '28g' else 10 if memory == '10g' else 1) * 1024**3 + 8 * 1024**3
    samples = [resource_sample(minimum_ram)]
    client = docker.from_env()
    container = None
    started = time.monotonic()
    deadline = datetime.fromisoformat(spec['session']['started_utc']) + timedelta(hours=8)
    try:
        container = client.containers.run(image, command, **args)
        while True:
            container.reload()
            if container.status in ('exited', 'dead'):
                break
            samples.append(resource_sample())
            if datetime.now(timezone.utc) + timedelta(minutes=15) >= deadline or time.monotonic() - started > 1500:
                raise TimeoutError('This benchmark reached its time limit')
            time.sleep(2)
        container.reload()
        state = container.attrs['State']
        receipt = {'container_id': container.id, 'image_id': container.attrs['Image'], 'image': image,
                   'command': command, 'entrypoint': entrypoint, 'exit_code': state['ExitCode'], 'oom_killed': state['OOMKilled'],
                   'elapsed_seconds': time.monotonic() - started, 'memory_limit_bytes': container.attrs['HostConfig']['Memory'],
                   'nano_cpus': container.attrs['HostConfig']['NanoCpus'],
                   'network_mode': container.attrs['HostConfig']['NetworkMode'],
                   'read_only_rootfs': container.attrs['HostConfig']['ReadonlyRootfs'],
                   'resources': samples, 'scope': 'Container elapsed includes startup; per-query timings are in the Spark benchmark report.'}
        save(receipt_path, receipt)
        if state['ExitCode'] or state['OOMKilled']:
            raise RuntimeError('Native Spark container failed; retained console and receipt identify the cause')
        return receipt
    finally:
        if container is not None:
            container.reload()
            if container.status not in ('exited', 'dead'):
                container.stop(timeout=20)
            suffix = 'console.txt' if entrypoint is None else 'metrics-console.txt'
            body = container.logs(stdout=True, stderr=True)
            (result_dir / suffix).write_bytes(body)
            print(body.decode('utf-8', errors='replace'), flush=True)
            container.remove()
        client.close()


def run_experiment(run_id: str, experiment: str) -> dict:
    """Execute real paired Spark jobs under the same Airflow pool, never replaying a saved report as work."""
    if experiment not in ('corrections', 'skew'):
        raise ValueError('Unknown benchmark experiment')
    root = Path(os.environ['IOT_ROOT']).resolve()
    target, spec = read_spec(root, run_id)
    check_sources(root, spec)
    phase = target / experiment
    phase.mkdir(exist_ok=False)
    phase.chmod(0o2770)
    data = root / 'data/benchmarks' / run_id / experiment
    cores, heap, memory, partitions = (4, '7g', '10g', 8) if experiment == 'corrections' else (6, '20g', '28g', 32)
    command = ['--master', f'local[{cores}]', '--driver-memory', heap, '--conf', 'spark.driver.bindAddress=127.0.0.1',
               '--conf', 'spark.driver.host=127.0.0.1', '--conf', 'spark.local.dir=/benchmark-scratch',
               '--conf', 'spark.driver.extraJavaOptions=-Djava.io.tmpdir=/benchmark-scratch',
               '--conf', 'spark.eventLog.enabled=true', '--conf', 'spark.eventLog.compress=false',
               '--conf', 'spark.eventLog.dir=file://' + str(root / 'data/benchmark-events' / run_id)]
    if experiment == 'corrections':
        command += [str(root / 'pipelines/data_corrections_benchmark.py'), '--source', str(root / spec['small_source'])]
    else:
        command += [str(root / 'pipelines/skew_job.py'), '--source', str(root / spec['skew_source']),
                    '--machines', str(root / spec['skew_machines']), '--binding', str(target / 'skew-binding.json'),
                    '--policy', str(root / 'pipelines/configs/factory-risk-policies.json')]
    command += ['--output', str(data), '--report', str(phase / 'benchmark.json'),
                '--partitions', str(partitions), '--repetitions', str(spec['repetitions'])]
    receipt = execute_container(root, target, spec, experiment, command, memory=memory, cpus=cores)
    report = json.loads((phase / 'benchmark.json').read_text())
    if not report['passed']:
        raise ValueError('Benchmark did not pass')
    check_sources(root, spec)
    return {'application_id': report['application_id'], 'container_id': receipt['container_id'], 'report': str(phase / 'benchmark.json')}


def validate_experiment(run_id: str, experiment: str) -> dict:
    """Parse completed event logs and fail Airflow if output, source or actual task metrics do not reconcile."""
    root = Path(os.environ['IOT_ROOT']).resolve()
    target, spec = read_spec(root, run_id)
    phase = target / experiment
    report = json.loads((phase / 'benchmark.json').read_text())
    marker = json.loads((root / 'data/benchmarks' / run_id / experiment / '_RUN_SUCCESS.json').read_text())
    if not report['passed'] or marker['report_sha256'] != digest(phase / 'benchmark.json'):
        raise ValueError('Success marker is missing or differs from actual report')
    app = report['application_id']
    if not re.fullmatch(r'local-[0-9]+', app):
        raise ValueError('Unexpected Spark application ID')
    log = root / 'data/benchmark-events' / run_id / app
    execute_container(root, target, spec, experiment,
                      ['-m', 'pipelines.skew_metrics', '--log', str(log), '--report', str(phase / 'task-metrics.json')],
                      memory='1g', cpus=1, entrypoint='python3')
    metrics = json.loads((phase / 'task-metrics.json').read_text())
    expected = spec['repetitions'] * (4 if experiment == 'corrections' else 2)
    if not metrics['application_ended'] or len(report['measurements']) != expected:
        raise ValueError('Incomplete measurement set or event log')
    methods = [('schema', 'inferred'), ('schema', 'declared'), ('dedup', 'window'), ('dedup', 'aggregate')] if experiment == 'corrections' else [('skew', 'baseline'), ('skew', 'broadcast')]
    wanted = {(name, strategy, repeat) for name, strategy in methods for repeat in range(spec['repetitions'])}
    measured = [(item.get('experiment', 'skew'), item['strategy'], item['repeat']) for item in report['measurements']]
    if len(set(measured)) != expected or set(measured) != wanted:
        raise ValueError('Measured strategies/repetitions are missing or duplicated')
    for item in report['measurements']:
        totals = metrics['groups'].get(item['group'], {}).get('totals', {})
        if totals.get('task_attempts', 0) < 1 or totals.get('failed_attempts', 1):
            raise ValueError('Missing measured group or failed Spark task')
    check_sources(root, spec)
    result = {'passed': True, 'checked_utc': utc_now(), 'application_id': app, 'input_and_code_unchanged': True,
              'measurements': expected, 'benchmark_sha256': digest(phase / 'benchmark.json'),
              'event_log_bytes': log.stat().st_size, 'event_log_sha256': digest(log),
              'task_metrics_sha256': digest(phase / 'task-metrics.json'),
              'scope': 'Paired experiments and actual completed Spark task metrics; no required speedup, full-scale or production promotion claim.'}
    save(phase / 'verification.json', result)
    return result
