"""Launch isolated Feast or DataHub SDK jobs for the trusted incremental materialization DAG."""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re

import docker


def execute(operation: str, run_id: str, end: str):
    """Accept a safe run name and UTC cutoff; keep SDKs and Redis access outside Airflow's environment."""
    if operation not in ('register', 'materialize', 'validate', 'publish') or not re.fullmatch(r'[a-z][a-z0-9-]{2,63}', run_id):
        raise ValueError('Invalid materialization operation/run name')
    cutoff = datetime.fromisoformat(end.replace('Z', '+00:00'))
    if cutoff.tzinfo is None or cutoff.utcoffset() != timedelta(0):
        raise ValueError('An explicit UTC cutoff is required')
    root = Path(os.environ['IOT_ROOT']).resolve()
    reports = '/workspace/outputs/feast-materialization/' + run_id
    metadata = operation == 'publish'
    command = ['--reports', reports]
    if not metadata:
        command += ['--operation', operation, '--end', cutoff.isoformat(),
                    '--source-state', '/workspace/data/feast/batch-medium-27',
                    '--batch-report', '/workspace/outputs/feast-batch-27/batch/report.json',
                    '--state', '/workspace/data/feast/airflow-medium-27',
                    '--protocol', '/workspace/ml/protocols/medium-60d-v1.json']
    volumes = {str(root / 'feature_store'): {'bind': '/workspace/feature_store', 'mode': 'ro'},
               str(root / 'ml'): {'bind': '/workspace/ml', 'mode': 'ro'},
               str(root / 'outputs'): {'bind': '/workspace/outputs', 'mode': 'rw'},
               str(root / 'data/feast'): {'bind': '/workspace/data/feast', 'mode': 'ro' if metadata else 'rw'},
               '/srv/iot-fast/feast-scratch': {'bind': '/workspace/feast-temp', 'mode': 'rw'}}
    client = docker.from_env()
    container = client.containers.run('iot-coursework-metadata:1.6.0' if metadata else 'iot-coursework-feast:0.67.0',
        command, entrypoint=['python', '-m', 'feature_store.metadata' if metadata else 'feature_store.materialize'],
        detach=True, working_dir='/workspace', network='iot-metadata' if metadata else 'iot-feast_default',
        environment={'PYTHONPATH': '/workspace', 'IOT_FEAST_REDIS': 'online:6379', 'FEAST_USAGE': 'False',
                     'IOT_FEAST_TEMP': '/workspace/feast-temp', 'DATAHUB_GMS_URL': 'http://datahub-gms:8080'},
        volumes=volumes, mem_limit='2g' if metadata else '8g', nano_cpus=1_000_000_000,
        labels={'iot.pipeline': 'feast-materialization', 'iot.run': run_id, 'iot.operation': operation})
    try:
        for line in container.logs(stream=True, follow=True):
            print(line.decode(errors='replace').rstrip(), flush=True)
        code = container.wait(timeout=600)['StatusCode']
        container.reload()
        print(json.dumps({'operation': operation, 'container_id': container.id, 'image_id': container.attrs['Image'], 'exit_code': code,
                          'memory_bytes': container.attrs['HostConfig']['Memory'], 'nano_cpus': container.attrs['HostConfig']['NanoCpus']}), flush=True)
        if code:
            raise RuntimeError('Native ' + operation + ' container failed; persisted reports remain available')
        return {'operation': operation, 'reports': reports, 'image_id': container.attrs['Image']}
    finally:
        container.remove(force=True)
        client.close()
