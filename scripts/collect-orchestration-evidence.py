"""Collect real VM service, DAG, integrity and DataHub readback evidence without copying datasets or secrets."""

import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipelines.orchestration_contract import digest, inventory, save, stage_path, utc_now, verified_manifest


def collect() -> None:
    """Assert success/failure/recovery states and copy only small reports plus code/image fingerprints."""
    spec = importlib.util.spec_from_file_location('verify_orchestration', ROOT / 'scripts/verify-orchestration.py')
    verify = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verify)
    destination = ROOT / 'outputs/orchestration/evidence'
    destination.mkdir(exist_ok=False)
    health = verify.health()
    if health['import_errors']['total_entries'] or any(health['airflow'].get(name, {}).get('status') != 'healthy'
                                                       for name in ('metadatabase', 'scheduler', 'dag_processor')):
        raise ValueError('Airflow health/import validation failed')
    checks = {}
    batches = ('orchestration-good-26', 'fixture-null-machine-26', 'orchestration-recovery-26')
    stages = ('dp1', 'dp2', 'dp3')
    for batch in batches:
        airflow = verify.snapshot(batch)
        states = [item['run']['state'] if item['run'] else None for item in airflow['dags'].values()]
        expected = ['failed', None, None] if batch.startswith('fixture-') else ['success'] * 3
        if states != expected:
            raise ValueError(f'Unexpected DAG states for {batch}: {states}')
        if batch.startswith('fixture-'):
            tasks = {t['task_id']: t['state'] for t in airflow['dags'][verify.DAGS[0]]['tasks']['task_instances']}
            if tasks != {'ingest': 'success', 'validate': 'failed', 'publish_to_datahub': 'success', 'release_next_pipeline': 'upstream_failed'}:
                raise ValueError('Fault fixture did not fail specifically at validation or did not block downstream')
        metadata = verify.metadata(batch)
        checks[batch] = {'dag_states': states, 'indexed_datasets': len(metadata['datasets']), 'stages': {}}
        for name in (f'{batch}-airflow.json', f'{batch}-datahub.json', f'{batch}-trigger.json'):
            shutil.copy2(ROOT / 'outputs/orchestration' / name, destination / name)
        for stage in stages:
            directory = stage_path(ROOT, batch, stage)
            if not directory.exists():
                continue
            manifest = verified_manifest(directory, require_validated=not batch.startswith('fixture-'))
            validation = json.loads((directory / 'validation.json').read_text())
            if validation['manifest_sha256'] != digest(directory / '_INGESTED.json'):
                raise ValueError('Validation binding differs')
            target = destination / batch / stage
            target.mkdir(parents=True)
            for name in ('_INGESTED.json', 'validation.json', 'datahub-publication.json'):
                shutil.copy2(directory / name, target / name)
            checks[batch]['stages'][stage] = {'passed': validation['passed'], 'checks': len(validation['checks']),
                                            'failed_rules': [c['name'] for c in validation['checks'] if not c['passed']],
                                            'tables': {name: value['rows'] for name, value in manifest['tables'].items()},
                                            'parquet_bytes': sum(f['bytes'] for f in manifest['files']),
                                            'source_manifest_sha256': manifest['source_manifest_sha256']}
    # Exercise a completed stage's actual retry path, including source/transform/file hashes.
    directory = stage_path(ROOT, 'orchestration-recovery-26', 'dp1')
    before = inventory(directory)
    command = ['docker', 'run', '--rm', '--network', 'iot-metadata', '--memory', '14g', '--cpus', '4',
               '-e', f'PYTHONPATH={ROOT}', '-v', f'{ROOT}:{ROOT}', '-w', str(ROOT),
               'iot-coursework-spark:3.5.9', '--master', 'local[4]', '--driver-memory', '10g',
               str(ROOT / 'pipelines/orchestrated_job.py'), '--stage', 'dp1', '--operation', 'ingest',
               '--batch', 'orchestration-recovery-26']
    with (destination / 'completed-stage-replay.txt').open('w') as log:
        replay = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    if replay.returncode or before != inventory(directory):
        raise ValueError('Completed-stage replay failed or changed Parquet bytes')
    checks['completed_stage_replay'] = {'exit_code': replay.returncode, 'same_part_hashes': True, 'files': len(before)}
    images = json.loads(subprocess.check_output(['docker', 'image', 'inspect', 'iot-coursework-airflow:3.1.7',
                       'iot-coursework-metadata:1.6.0', 'iot-coursework-spark:3.5.9'], text=True))
    image_info = [{'tags': item['RepoTags'], 'id': item['Id'], 'repo_digests': item['RepoDigests']} for item in images]
    source_files = [p for folder in ('generator', 'pipelines', 'ml', 'tests', 'airflow', 'infra', 'scripts')
                    for p in (ROOT / folder).rglob('*') if p.is_file() and (p.suffix in ('.py', '.yaml', '.yml') or p.name == 'Dockerfile')
                    and '__pycache__' not in p.parts and '.terraform' not in p.parts]
    source_hashes = [{'path': p.relative_to(ROOT).as_posix(), 'sha256': digest(p)} for p in sorted(source_files)]
    package = subprocess.check_output(['docker', 'run', '--rm', '--entrypoint', 'python', 'iot-coursework-metadata:1.6.0',
                                        '-m', 'pip', 'show', 'acryl-datahub'], text=True)
    sdk = next(line.split(': ', 1)[1] for line in package.splitlines() if line.startswith('Version: '))
    version = subprocess.check_output(['docker', 'exec', 'iot-orchestration-airflow-api-1', 'airflow', 'version'], text=True).strip().splitlines()[-1]
    save(destination / 'runtime.json', {'checked_utc': utc_now(), 'machine': 'iot-cw-vm', 'cpu_count': os.cpu_count(),
                                      'python_host': platform.python_version(), 'airflow': version, 'datahub_sdk': sdk,
                                      'images': image_info, 'source_files': source_hashes,
                                      'disk_free_bytes': {path: shutil.disk_usage(path).free for path in ('/srv/iot-data', '/srv/iot-fast')},
                                      'meminfo': Path('/proc/meminfo').read_text().splitlines()[:3],
                                      'runtime_source': 'Working-tree snapshot over IAP; Git commit mapping verified on PC after commit.'})
    for name in ('health.json', 'source-receipt.json', 'unit-tests.txt'):
        shutil.copy2(ROOT / 'outputs/orchestration' / name, destination / name)
    save(destination / 'verification.json', {'passed': True, 'verified_utc': utc_now(), 'checks': checks,
                                            'scope': '20 machines / 48 continuous hours, actual Airflow scheduler + Spark + DataHub OSS; no 100GB or model-quality claim.'})
    print(json.dumps(checks, indent=2))


if __name__ == '__main__':
    os.chdir(ROOT)
    collect()
