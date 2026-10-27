"""Run packaged Feast initial/incremental/replay jobs from the tested CI release."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from airflow.sdk import DAG, get_current_context
from airflow.providers.standard.operators.python import PythonOperator
import docker


def execute(operation, phase):
    """Bind every native container to its release image and isolated checkpoint/Redis project."""
    conf = get_current_context()['dag_run'].conf
    release = conf['release']
    if not re.fullmatch(r'mat-jenkins-[1-9][0-9]*', release):
        raise ValueError('Invalid materialization CI release')
    root = Path(os.environ['IOT_ROOT'])
    directory = root/'data/ci/releases'/release
    manifest = json.loads((directory/'release.json').read_text())
    if manifest['commit'] != conf['commit'] or manifest['release'] != release:
        raise ValueError('Release source differs from deployed DAG configuration')
    if sha256(Path(__file__).read_bytes()).hexdigest() != manifest['dag_sha256']:
        raise ValueError('Deployed DAG differs from the tested source')
    project = 'iot_ci_mat_' + release.removeprefix('mat-jenkins-')
    reports = '/workspace/outputs/' + phase
    publish = operation == 'publish'
    command = ['--reports', reports]
    if publish:
        command += ['--dag', 'iot_ci_materialization', '--run-id', release]
    else:
        command += ['--operation', operation, '--project', project,
            '--end', '2026-11-29T23:00:00Z' if phase == 'initial' else '2026-11-30T00:00:00Z',
            '--source-state', '/workspace/data/feast/batch-medium-27',
            '--batch-report', '/workspace/source-reports/batch/report.json',
            '--state', '/workspace/state/registry-state',
            '--protocol', '/opt/iot-release/ml/protocols/medium-60d-v1.json']
    client = docker.from_env()
    container = client.containers.run(manifest['images']['metadata' if publish else 'feast']['id'], command,
        entrypoint=['python', '-m', 'feature_store.metadata' if publish else 'feature_store.materialize'],
        detach=True, working_dir='/workspace', network='iot-metadata' if publish else 'iot-feast_default',
        environment={'PYTHONPATH': '/opt/iot-release', 'IOT_FEAST_REDIS': 'online:6379', 'FEAST_USAGE': 'False',
            'DATAHUB_GMS_URL': 'http://datahub-gms:8080'},
        volumes={str(root/'data/feast/batch-medium-27'): {'bind':'/workspace/data/feast/batch-medium-27','mode':'ro'},
            str(root/'outputs/feast-batch-27'): {'bind':'/workspace/source-reports','mode':'ro'},
            str(directory/'state'): {'bind':'/workspace/state','mode':'rw'},
            str(directory/'outputs'): {'bind':'/workspace/outputs','mode':'rw'}},
        mem_limit='2g' if publish else '8g', nano_cpus=1_000_000_000,
        labels={'iot.ci.release':release,'iot.operation':operation,'iot.phase':phase})
    try:
        for line in container.logs(stream=True, follow=True):
            print(line.decode(errors='replace').rstrip(), flush=True)
        status = container.wait(timeout=600)['StatusCode']
        container.reload()
        value = container.attrs
        receipt = {'container_id':container.id,'image_id':value['Image'],'exit_code':status,
            'oom_killed':value['State']['OOMKilled'],'started_utc':value['State']['StartedAt'],
            'finished_utc':value['State']['FinishedAt'],'memory_bytes':value['HostConfig']['Memory'],
            'nano_cpus':value['HostConfig']['NanoCpus'],'commit':manifest['commit'],
            'release':release,'project':project,'operation':operation,'phase':phase}
        (directory/'outputs'/f'{phase}-{operation}-container.json').write_text(json.dumps(receipt,indent=2)+'\n')
        if status:
            raise RuntimeError('Native materialization CI container failed; preserve its receipts and logs')
    finally:
        container.remove(force=True)
        client.close()


with DAG(dag_id='iot_ci_materialization', schedule=None, catchup=False, max_active_runs=1,
         start_date=datetime(2026,10,1,tzinfo=timezone.utc), is_paused_upon_creation=False,
         tags=['iot','ci','materialization'], default_args={'retries':0,'execution_timeout':timedelta(minutes=10)}) as dag:
    previous = None
    for task,operation,phase in [('register_bound_source','register','initial'),
            ('initial_materialize','materialize','initial'),('validate_initial','validate','initial'),
            ('incremental_materialize','materialize','incremental'),('validate_incremental','validate','incremental'),
            ('replay_checkpoint','materialize','replay'),('validate_replay','validate','replay'),
            ('publish_to_datahub','publish','incremental')]:
        current = PythonOperator(task_id=task,python_callable=execute,
            op_kwargs={'operation':operation,'phase':phase},pool='feast_jobs')
        if previous:
            previous >> current
        previous = current
