"""Deploy versioned DP1-DP3 jobs from immutable CI release manifests on the private Airflow instance."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from airflow.sdk import DAG, get_current_context
from airflow.providers.standard.operators.python import PythonOperator
from airflow.utils.trigger_rule import TriggerRule
import docker


def execute(stage, operation):
    """Run the packaged source with immutable inputs; propagate failure and retain the real container receipt."""
    config = get_current_context()['dag_run'].conf
    release = config['release']
    if not re.fullmatch(r'gha-[0-9]+-[0-9]+', release):
        raise ValueError('Invalid CI release ID')
    root = Path(os.environ['IOT_ROOT'])
    directory = root/'data/ci/releases'/release
    manifest = json.loads((directory/'release.json').read_text())
    if manifest['release'] != release or config['commit'] != manifest['commit']:
        raise ValueError('CI run does not match the deployed source commit')
    work = directory/'work'
    for name, expected in manifest['source_sha256'].items():
        if sha256((work/name).read_bytes()).hexdigest() != expected:
            raise ValueError('Deployed source changed: '+name)
    publish = operation == 'publish'
    image = manifest['images']['metadata' if publish else 'spark']['id']
    command = ['--stage', stage, '--batch', release]
    if not publish:
        command = ['--master', 'local[2]', '--driver-memory', '3g',
                   '--conf', 'spark.sql.shuffle.partitions=8',
                   '--conf', 'spark.sql.adaptive.enabled=true',
                   '--conf', 'spark.local.dir=/tmp/spark',
                   '/opt/iot-release/pipelines/orchestrated_job.py',
                   *command, '--operation', operation]
    volumes = {str(work): {'bind': '/work', 'mode': 'ro'},
               str(root/'data/source/orchestration-pilot'): {'bind': '/work/data/source/orchestration-pilot', 'mode': 'ro'},
               str(work/'data/orchestrated'): {'bind': '/work/data/orchestrated', 'mode': 'rw'},
               str(work/'outputs'): {'bind': '/work/outputs', 'mode': 'rw'},
               str(Path('/srv/iot-fast/ci')/release): {'bind': '/tmp/spark', 'mode': 'rw'}}
    client = docker.from_env()
    container = client.containers.run(image, command, detach=True, working_dir='/work',
        environment={'PYTHONPATH': '/opt/iot-release', 'DATAHUB_GMS_URL': 'http://datahub-gms:8080'},
        volumes=volumes, network='iot-metadata', mem_limit='2g' if publish else '5g',
        nano_cpus=1_000_000_000 if publish else 2_000_000_000,
        labels={'iot.ci.release': release, 'iot.stage': stage, 'iot.operation': operation})
    receipt = work/'outputs'/f'{stage}-{operation}-container.json'
    try:
        for line in container.logs(stream=True, follow=True):
            print(line.decode(errors='replace').rstrip(), flush=True)
        status = container.wait(timeout=1500)['StatusCode']
        container.reload()
        state = container.attrs
        receipt.write_text(json.dumps({'container_id': container.id, 'image_id': state['Image'],
            'exit_code': status, 'oom_killed': state['State']['OOMKilled'],
            'started_utc': state['State']['StartedAt'], 'finished_utc': state['State']['FinishedAt'],
            'memory_limit': state['HostConfig']['Memory'], 'nano_cpus': state['HostConfig']['NanoCpus'],
            'commit': manifest['commit'], 'release': release, 'stage': stage, 'operation': operation}, indent=2)+'\n')
        if status:
            raise RuntimeError(f'CI {stage}/{operation} exited {status}; preserve its native logs and artifacts')
    finally:
        container.remove(force=True)
        client.close()


for stage, dag_id in [('dp1', 'iot_ci_dp1_bronze'), ('dp2', 'iot_ci_dp2_warehouse'), ('dp3', 'iot_ci_dp3_features')]:
    with DAG(dag_id=dag_id, schedule=None, start_date=datetime(2026, 10, 1, tzinfo=timezone.utc),
             catchup=False, max_active_runs=1, is_paused_upon_creation=False, tags=['iot', 'ci', stage],
             default_args={'retries': 0, 'execution_timeout': timedelta(minutes=25)}) as dag:
        ingest = PythonOperator(task_id='ingest', python_callable=execute,
            op_kwargs={'stage': stage, 'operation': 'ingest'}, pool='spark_jobs')
        validate = PythonOperator(task_id='validate', python_callable=execute,
            op_kwargs={'stage': stage, 'operation': 'validate'}, pool='spark_jobs')
        publish = PythonOperator(task_id='publish_to_datahub', python_callable=execute,
            op_kwargs={'stage': stage, 'operation': 'publish'}, trigger_rule=TriggerRule.ALL_DONE)
        ingest >> validate >> publish
    globals()[dag_id] = dag
