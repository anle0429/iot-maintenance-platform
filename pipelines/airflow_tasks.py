"""Run bounded sibling containers from trusted Airflow DAGs without installing Spark or DataHub SDK in Airflow."""

import json
import os
from pathlib import Path

import docker

from pipelines.orchestration_contract import stage_path


def execute_stage(stage: str, operation: str, batch: str, fault: str = '') -> dict:
    """Run one isolated job; nonzero exits propagate to the real Airflow task state."""
    root = Path(os.environ['IOT_ROOT']).resolve()
    target = stage_path(root, batch, stage)
    if fault not in ('', 'null_machine') or (fault and not batch.startswith('fixture-')):
        raise ValueError('invalid fault fixture')
    if operation not in ('ingest', 'validate', 'publish'):
        raise ValueError('invalid operation')
    metadata = operation == 'publish'
    image = 'iot-coursework-metadata:1.6.0' if metadata else 'iot-coursework-spark:3.5.9'
    command = ['--stage', stage, '--batch', batch]
    if not metadata:
        command = ['--master', 'local[4]', '--driver-memory', '10g', '--conf', 'spark.sql.shuffle.partitions=8',
                   '--conf', 'spark.sql.adaptive.enabled=true', '--conf', 'spark.driver.bindAddress=0.0.0.0',
                   '--conf', 'spark.local.dir=/tmp/spark', str(root / 'pipelines/orchestrated_job.py'),
                   *command, '--operation', operation, '--fault', fault]
    client = docker.from_env()
    container = client.containers.run(image, command, detach=True, working_dir=str(root),
                                      environment={'PYTHONPATH': str(root), 'DATAHUB_GMS_URL': 'http://datahub-gms:8080'},
                                      volumes={str(root): {'bind': str(root), 'mode': 'rw'},
                                               '/srv/iot-fast/orchestration-scratch': {'bind': '/tmp/spark', 'mode': 'rw'}},
                                      network='iot-metadata', mem_limit='2g' if metadata else '14g',
                                      nano_cpus=2_000_000_000 if metadata else 4_000_000_000,
                                      labels={'iot.batch': batch, 'iot.stage': stage, 'iot.operation': operation})
    try:
        for line in container.logs(stream=True, follow=True):
            print(line.decode('utf-8', errors='replace').rstrip(), flush=True)
        status = container.wait(timeout=1800)['StatusCode']
        info = container.attrs
        print(json.dumps({'container_id': container.id, 'image_id': info['Image'], 'exit_code': status}), flush=True)
        if status:
            raise RuntimeError(f'{stage}/{operation} container exited {status}; persisted validation report remains available')
        report_name = {'ingest': '_INGESTED.json', 'validate': 'validation.json', 'publish': 'datahub-publication.json'}[operation]
        return {'report': str(target / report_name), 'container_id': container.id, 'image_id': info['Image']}
    finally:
        container.remove(force=True)
        client.close()
