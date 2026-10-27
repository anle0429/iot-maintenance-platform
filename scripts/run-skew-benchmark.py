"""Run an immutable Silver join benchmark on the existing VM with HDD scratch and live resource/time guards."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generator.chunked import digest_file
from pipelines.factory_policy import load_policy
from pipelines.orchestration_contract import save, utc_now


def available() -> dict:
    """Measure actual filesystem reserves and reclaimable RAM without exporting cloud account identifiers."""
    values = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    return {'checked_utc': utc_now(), 'ram_bytes': int(values['MemAvailable'].split()[0]) * 1024,
            'free_bytes': {path: shutil.disk_usage(path).free for path in ('/srv/iot-data', '/srv/iot-fast')}}


def tree_bytes(path: Path) -> int:
    """Sample transient scratch safely even when Spark removes a file between enumeration and stat."""
    total = 0
    for directory, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(directory) / name).stat().st_size
            except FileNotFoundError:
                pass
    return total


def fingerprints(directory: Path) -> list[dict]:
    """Bind the actual current Parquet input; both measured strategies must consume identical immutable files."""
    if directory.is_symlink() or not directory.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('benchmark input must remain inside this project')
    files = sorted(directory.rglob('*.parquet'))
    if not files or any(path.is_symlink() for path in files):
        raise ValueError('missing or unsafe input Parquet files')
    return [{'path': path.relative_to(ROOT).as_posix(), 'bytes': path.stat().st_size, 'sha256': digest_file(path)} for path in files]


def run(args) -> None:
    """Keep all outputs new, monitor only this container and preserve any failed attempt rather than replacing it."""
    if platform.system() != 'Linux' or not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', args.run_name):
        raise ValueError('use a valid fresh run name on the approved Linux VM')
    os.chdir(ROOT)
    for mount in ('/srv/iot-data', '/srv/iot-fast'):
        if not Path(mount).is_mount():
            raise ValueError('required coursework filesystem is not mounted')
    deadline = datetime.fromisoformat(args.deadline_utc.replace('Z', '+00:00')).timestamp()
    before = available()
    if os.cpu_count() < 7 or before['ram_bytes'] < 36 * 1024**3:
        raise ValueError('insufficient CPU or available RAM for the existing 6CPU/28GiB container')
    if before['free_bytes']['/srv/iot-data'] < 310_000_000_000 or before['free_bytes']['/srv/iot-fast'] < 35_000_000_000:
        raise ValueError('insufficient HDD/SSD reserve')
    if time.time() + 2700 + 1200 > deadline:
        raise ValueError('45-minute bounded benchmark plus 20-minute finish reserve does not fit this VM session')
    source_run = 'scale-ingest-medium-26'
    original = json.loads((ROOT / 'outputs' / source_run / 'ingest-report.json').read_text())
    receipt = json.loads((ROOT / 'outputs/scale-intermediate-26/receipt.json').read_text())
    if not original['passed'] or original['source_manifest_sha256'] != receipt['source_manifest_sha256']:
        raise ValueError('intermediate input is not a matching validated source')
    source = ROOT / 'data/lakehouse' / source_run / 'silver/stg_telemetry'
    machines = ROOT / 'data/lakehouse' / source_run / 'bronze/raw_machines'
    if not (ROOT / 'data/lakehouse' / source_run / '_RUN_SUCCESS.json').is_file():
        raise ValueError('intermediate materialization has no success marker')
    output, data = ROOT / 'outputs' / args.run_name, ROOT / 'data/benchmarks' / args.run_name
    scratch = Path('/srv/iot-data/spark-scratch') / args.run_name
    if any(path.exists() for path in (output, data, scratch)):
        raise ValueError('benchmark run already exists; choose another name')
    policy_path = ROOT / 'pipelines/configs/factory-risk-policies.json'
    load_policy(policy_path)
    source_hashes = fingerprints(source)
    machine_hashes = fingerprints(machines)
    output.mkdir(parents=True)
    output.chmod(0o777)
    data.parent.mkdir(parents=True, exist_ok=True)
    data.parent.chmod(0o1777)
    scratch.mkdir(parents=True)
    scratch.chmod(0o1777)
    binding = {'source_run': source_run, 'source_manifest_sha256': original['source_manifest_sha256'],
               'raw_offline_bytes': original['source_offline_bytes'], 'unique_rows': original['silver_unique_rows'],
               'machine_count': receipt['quality']['config']['machine_count'], 'machine_files': machine_hashes,
               'factory_unique_counts': receipt['quality']['factory_unique_counts'], 'input_files': source_hashes,
               'input_parquet_bytes': sum(item['bytes'] for item in source_hashes),
               'source_receipt_sha256': digest_file(ROOT / 'outputs/scale-intermediate-26/receipt.json'),
               'policy_sha256': digest_file(policy_path)}
    save(output / 'input-binding.json', binding)
    env = os.environ.copy()
    env.update(IOT_INGEST_CPUS='6', IOT_INGEST_MEMORY=str(28 * 1024**3), IOT_INGEST_SCRATCH=str(scratch))
    compose = ['docker', 'compose', '-f', 'infra/ingest.compose.yaml', '-f', 'infra/ingest.cloud.compose.yaml']
    event_directory = ROOT / 'data/ingest-events'
    event_directory.mkdir(exist_ok=True)
    event_directory.chmod(0o1777)
    commands = []

    def command(argv: list[str], name: str, guarded=False) -> None:
        """Record real exits and stop only this run's container on a resource or session-time violation."""
        started = time.perf_counter()
        with (output / name).open('w') as handle:
            process = subprocess.Popen(argv, env=env, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
            observed = {'samples': 0, 'peak_scratch_file_bytes': 0, 'minimum_available_ram_bytes': before['ram_bytes'],
                        'minimum_hdd_free_bytes': before['free_bytes']['/srv/iot-data']}
            try:
                while process.poll() is None:
                    if guarded:
                        current = available()
                        size = tree_bytes(scratch)
                        observed['samples'] += 1
                        observed['peak_scratch_file_bytes'] = max(size, observed['peak_scratch_file_bytes'])
                        observed['minimum_available_ram_bytes'] = min(observed['minimum_available_ram_bytes'], current['ram_bytes'])
                        observed['minimum_hdd_free_bytes'] = min(observed['minimum_hdd_free_bytes'], current['free_bytes']['/srv/iot-data'])
                        save(output / 'resource-samples.json', {**observed, 'checked_utc': utc_now(), 'finished': False})
                        if (current['ram_bytes'] < 4 * 1024**3 or current['free_bytes']['/srv/iot-data'] < 285_000_000_000 or
                                current['free_bytes']['/srv/iot-fast'] < 35_000_000_000 or size > 20_000_000_000 or
                                time.time() + 600 > deadline or time.perf_counter() - started > 2700):
                            raise RuntimeError('benchmark resource/time guard reached; retain partial outputs')
                    time.sleep(1)
            finally:
                if process.poll() is None:
                    subprocess.run(['docker', 'stop', '--time', '20', 'iot-' + args.run_name], capture_output=True)
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=30)
            commands.append({'argv': argv, 'exit_code': process.returncode, 'seconds': time.perf_counter() - started})
            save(output / 'commands.json', commands)
            if guarded:
                save(output / 'resource-samples.json', {**observed, 'finished': True, 'exit_code': process.returncode,
                                                       'scope': 'One-second samples; short scratch/RAM spikes may be missed.'})
            if process.returncode:
                raise RuntimeError(f'command exited {process.returncode}; inspect {name}')

    runtime_paths = [*sorted((ROOT / 'generator').glob('*.py')), *sorted((ROOT / 'pipelines').glob('*.py')),
                     *sorted((ROOT / 'tests').glob('*.py')), Path(__file__), policy_path,
                     ROOT / 'infra/ingest.compose.yaml', ROOT / 'infra/ingest.cloud.compose.yaml', ROOT / 'infra/spark/Dockerfile']
    image = subprocess.check_output(['docker', 'image', 'inspect', 'iot-coursework-spark:3.5.9', '--format', '{{.Id}}'], text=True).strip()
    save(output / 'preflight.json', {'checked_utc': utc_now(), 'resources': before, 'deadline_utc': args.deadline_utc,
         'scratch': str(scratch), 'scratch_cap_bytes': 20_000_000_000, 'hdd_minimum_free_bytes': 285_000_000_000,
         'container_bytes': 28 * 1024**3, 'cpu': 6, 'heap_gib': 20, 'partitions': args.partitions,
         'repetitions': args.repetitions, 'image_id': image, 'base_commit': args.base_commit,
         'runtime_files': [{'path': path.relative_to(ROOT).as_posix(), 'sha256': digest_file(path)} for path in runtime_paths],
         'scope': 'Measured intermediate Silver query only. No source regeneration, full100GB Spark or VM session extension.'})
    command([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], 'unit-tests.txt')
    command(compose + ['config', '--format', 'json'], 'resolved-compose.json')
    resolved = json.loads((output / 'resolved-compose.json').read_text())['services']['ingest']
    if int(resolved['mem_limit']) != 28 * 1024**3 or float(resolved['cpus']) != 6:
        raise ValueError('resolved container limits differ from the reviewed benchmark')
    container_source = source.relative_to(ROOT).as_posix()
    command(compose + ['run', '--rm', '--service-ports', '--name', 'iot-' + args.run_name, 'ingest',
        '--master', 'local[6]', '--driver-memory', '20g', '--conf', 'spark.driver.bindAddress=0.0.0.0',
        '--conf', 'spark.driver.host=127.0.0.1', '--conf', f'spark.local.dir=/scratch/{args.run_name}',
        '--conf', 'spark.eventLog.enabled=true', '--conf', 'spark.eventLog.dir=file:///workspace/data/ingest-events',
        'pipelines/skew_job.py', '--source', container_source, '--machines', machines.relative_to(ROOT).as_posix(),
        '--binding', f'outputs/{args.run_name}/input-binding.json',
        '--policy', 'pipelines/configs/factory-risk-policies.json', '--output', f'data/benchmarks/{args.run_name}',
        '--report', f'outputs/{args.run_name}/benchmark.json', '--partitions', str(args.partitions),
        '--repetitions', str(args.repetitions)], 'spark-console.txt', guarded=True)
    report = json.loads((output / 'benchmark.json').read_text())
    marker = json.loads((data / '_RUN_SUCCESS.json').read_text())
    if not report['passed'] or marker['report_sha256'] != digest_file(output / 'benchmark.json'):
        raise ValueError('benchmark success/report binding differs')
    if (fingerprints(source) != source_hashes or fingerprints(machines) != machine_hashes or
            digest_file(policy_path) != binding['policy_sha256']):
        raise ValueError('input changed during paired measurements')
    command(compose + ['run', '--rm', '--entrypoint', 'python3', 'ingest', '-m', 'pipelines.skew_metrics',
                       '--log', f"data/ingest-events/{report['application_id']}",
                       '--report', f'outputs/{args.run_name}/task-metrics.json'], 'metrics-console.txt')
    metrics = json.loads((output / 'task-metrics.json').read_text())
    for measured in report['measurements']:
        if measured['group'] not in metrics['groups'] or metrics['groups'][measured['group']]['totals']['failed_attempts']:
            raise ValueError('missing grouped metrics or failed task attempt')
    result = {'passed': True, 'completed_utc': utc_now(), 'application_id': report['application_id'],
              'benchmark_sha256': digest_file(output / 'benchmark.json'), 'task_metrics_sha256': digest_file(output / 'task-metrics.json'),
              'input_unchanged': True, 'after': available(), 'scope': report['scope']}
    save(output / 'verification.json', result)
    save(output / '_RUN_SUCCESS.json', {'verification_sha256': digest_file(output / 'verification.json')})
    print(json.dumps({'passed': True, 'application_id': report['application_id'], 'median_seconds': report['median_seconds'],
                      'observed_speedup': report['observed_speedup']}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-name', required=True)
    parser.add_argument('--deadline-utc', required=True)
    parser.add_argument('--base-commit', required=True)
    parser.add_argument('--partitions', type=int, default=32)
    parser.add_argument('--repetitions', type=int, default=3)
    arguments = parser.parse_args()
    if not 2 <= arguments.partitions <= 64 or not 1 <= arguments.repetitions <= 5:
        raise ValueError('invalid bounded partition/repetition count')
    run(arguments)
