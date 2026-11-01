"""Prepare immutable benchmark inputs on the approved CPU VM and trigger the real Airflow benchmark DAG."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipelines.benchmark_airflow import resource_sample, run_directory
from pipelines.orchestration_contract import digest, save, utc_now

RUNTIME_PATHS = ['airflow/dags/iot_data_benchmarks.py', 'pipelines/__init__.py', 'pipelines/benchmark_airflow.py',
                 'pipelines/data_corrections_benchmark.py', 'pipelines/skew_job.py', 'pipelines/skew_metrics.py',
                 'pipelines/orchestration_contract.py', 'pipelines/factory_policy.py', 'pipelines/transforms.py',
                 'pipelines/configs/factory-risk-policies.json', 'generator/__init__.py', 'generator/storage.py',
                 'generator/config.py', 'generator/simulation.py',
                 'scripts/run-data-benchmarks.py', 'infra/spark/Dockerfile']


def inventory(paths: list[Path]) -> list[dict]:
    """Bind only explicitly selected regular files inside this project, preserving original input bytes."""
    result = []
    for path in sorted(set(paths)):
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise ValueError('Input is missing or escapes the project')
        result.append({'path': path.relative_to(ROOT).as_posix(), 'bytes': path.stat().st_size, 'sha256': digest(path)})
    return result


def owned_directory(path: Path) -> None:
    """Create a fresh shared-group directory; never recursively change an existing dataset or service directory."""
    path.mkdir(parents=True, exist_ok=False)
    os.chown(path, 50000, 0)
    path.chmod(0o2770)


def prepare(args) -> dict:
    """Validate the actual eight-hour CPU descriptor and retained source, then seal a new experiment specification."""
    if os.name != 'posix' or os.geteuid() != 0:
        raise ValueError('Prepare on the approved Linux VM with root directory-creation privileges')
    root = ROOT.resolve()
    if not all(Path(p).is_mount() for p in ['/srv/iot-data', '/srv/iot-fast']):
        raise ValueError('Data and scratch volumes must be mounted')
    descriptor = json.loads(args.descriptor.read_text(encoding='utf-8-sig'))
    scheduling = descriptor['scheduling']
    if (descriptor['name'] != 'iot-cw-vm' or descriptor['status'] != 'RUNNING'
            or not descriptor['machineType'].endswith('/e2-highmem-8')
            or scheduling['maxRunDuration']['seconds'] != '28800' or scheduling['instanceTerminationAction'] != 'STOP'):
        raise ValueError('The existing eight-hour CPU session is required')
    session = {'name': descriptor['name'], 'machine_type': 'e2-highmem-8', 'started_utc': descriptor['lastStartTimestamp'],
               'max_seconds': 28800, 'termination_action': 'STOP'}
    from datetime import timedelta
    if datetime.now(timezone.utc) + timedelta(minutes=45) >= datetime.fromisoformat(session['started_utc']) + timedelta(hours=8):
        raise ValueError('Keep enough session time for a bounded experiment and finalization')
    resources = resource_sample(36 * 1024**3)
    source = args.source.resolve()
    if not source.is_relative_to(root / 'data/source') or source.is_symlink():
        raise ValueError('Use an existing generated small source inside data/source')
    small_files = sorted(source.glob('telemetry/schema_v*/part-*.jsonl'))
    if len(small_files) != 2 or sum(p.stat().st_size for p in small_files) > 100_000_000:
        raise ValueError('This correction experiment requires the bounded two-version development source')
    small_manifest = source / 'manifest.json'
    manifest = json.loads(small_manifest.read_text())
    if manifest['config']['machine_count'] != 20:
        raise ValueError('Use the measured development cohort, not a large generated source')
    source_run = 'scale-ingest-medium-26'
    original_path = root / 'outputs' / source_run / 'ingest-report.json'
    receipt_path = root / 'outputs/scale-intermediate-26/receipt.json'
    original, receipt = [json.loads(p.read_text()) for p in [original_path, receipt_path]]
    if not original['passed'] or original['source_manifest_sha256'] != receipt['source_manifest_sha256']:
        raise ValueError('The intermediate Silver source is not validated')
    silver = root / 'data/lakehouse' / source_run / 'silver/stg_telemetry'
    machines = root / 'data/lakehouse' / source_run / 'bronze/raw_machines'
    marker = root / 'data/lakehouse' / source_run / '_RUN_SUCCESS.json'
    if not marker.is_file() or original['silver_unique_rows'] != 17_280_000:
        raise ValueError('Validated intermediate source or success marker missing')
    source_manifest = json.loads(args.source_manifest.read_text())
    runtime = inventory([root / p for p in RUNTIME_PATHS])
    expected = {item['path']: item['sha256'] for item in source_manifest['files']}
    if any(expected.get(item['path']) != item['sha256'] for item in runtime):
        raise ValueError('Runtime source differs from the deployed source packet')
    output = run_directory(root, args.run_id)
    locations = [output, root / 'data/benchmarks' / args.run_id,
                 root / 'data/benchmark-events' / args.run_id, root / 'data/benchmark-scratch' / args.run_id]
    if any(p.exists() or p.is_symlink() for p in locations):
        raise ValueError('Benchmark paths already exist; preserve them and choose another ID')
    for path in locations:
        owned_directory(path)
    silver_files, machine_files = [inventory(list(p.glob('*.parquet'))) for p in [silver, machines]]
    if len(silver_files) != 64 or len(machine_files) != 1:
        raise ValueError('Intermediate source inventory differs from the recorded materialization')
    binding = {'source_run': source_run, 'source_manifest_sha256': original['source_manifest_sha256'],
               'raw_offline_bytes': original['source_offline_bytes'], 'unique_rows': original['silver_unique_rows'],
               'machine_count': receipt['quality']['config']['machine_count'], 'machine_files': machine_files,
               'factory_unique_counts': receipt['quality']['factory_unique_counts'], 'input_files': silver_files,
               'input_parquet_bytes': sum(i['bytes'] for i in silver_files), 'source_receipt_sha256': digest(receipt_path),
               'policy_sha256': digest(root / 'pipelines/configs/factory-risk-policies.json')}
    save(output / 'skew-binding.json', binding)
    spec = {'run_id': args.run_id, 'prepared_utc': utc_now(), 'repetitions': args.repetitions, 'session': session,
            'small_source': source.relative_to(root).as_posix(), 'skew_source': silver.relative_to(root).as_posix(),
            'skew_machines': machines.relative_to(root).as_posix(), 'runtime_files': runtime,
            'input_files': inventory(small_files + [small_manifest, original_path, receipt_path, marker, output / 'skew-binding.json']) + silver_files + machine_files,
            'source_packet': source_manifest, 'resources': resources,
            'scope': 'Separate small schema/dedup and retained 17.28M Silver join experiments. Fixed resources within each pair; no full100GB rerun.'}
    save(output / 'spec.json', spec)
    os.chown(output / 'spec.json', 50000, 0)
    return {'benchmark_id': args.run_id, 'repetitions': args.repetitions, 'runtime_files': len(runtime), 'input_files': len(spec['input_files'])}


def api(path: str, method='GET', body=None, token=None):
    """Use the existing private Airflow account, retaining access tokens only in memory."""
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = Request('http://127.0.0.1:18480' + path, headers=headers, method=method,
                      data=json.dumps(body).encode() if body is not None else None)
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError('Airflow request returned HTTP ' + str(error.code)) from None


def trigger(run_id: str) -> dict:
    """Trigger the actual DAG through its API; a queued run is not reported as successful execution."""
    passwords = json.loads((ROOT / 'data/runtime/secrets/orchestration-users.json').read_text())
    token = api('/auth/token', 'POST', {'username': 'learner', 'password': passwords['airflow']})['access_token']
    pool = api('/api/v2/pools/spark_jobs', token=token)
    if pool['slots'] != 1:
        raise ValueError('Use the existing single-slot Spark pool')
    dag = 'iot_data_benchmarks'
    api('/api/v2/dags/' + dag, 'PATCH', {'is_paused': False}, token)
    result = api('/api/v2/dags/' + dag + '/dagRuns', 'POST',
                 {'dag_run_id': run_id, 'logical_date': datetime.now(timezone.utc).isoformat(), 'conf': {'benchmark_id': run_id}}, token)
    save(run_directory(ROOT, run_id) / 'airflow-trigger.json', result)
    return {'triggered': run_id, 'state': result['state']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-manifest', type=Path, required=True)
    parser.add_argument('--descriptor', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, choices=[1, 3], default=3)
    parser.add_argument('--prepare-only', action='store_true')
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments), indent=2), flush=True)
    if not arguments.prepare_only:
        print(json.dumps(trigger(arguments.run_id), indent=2), flush=True)
