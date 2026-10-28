"""Deploy distinct Airflow offline-history and online-latest jobs from the tested immutable CI release."""
from datetime import datetime,timedelta,timezone
from hashlib import sha256
from pathlib import Path
import json,os,re
import docker
from airflow.sdk import DAG,get_current_context
from airflow.providers.standard.operators.python import PythonOperator


def execute(destination,operation):
    """Run the packaged native job with independent state; online mounts upstream Parquet read-only."""
    conf=get_current_context()['dag_run'].conf;release=conf['release'];commit=conf['commit']
    if re.fullmatch('stream-'+destination+r'-jenkins-[1-9][0-9]{0,9}',release) is None:raise ValueError('Invalid stream CI release')
    root=Path(os.environ['IOT_ROOT']);directory=root/'data/ci/releases'/release;manifest=json.loads((directory/'release.json').read_bytes())
    if (manifest['release']!=release or manifest['commit']!=commit or manifest['destination']!=destination
            or sha256(Path(__file__).read_bytes()).hexdigest()!=manifest['dag_sha256']):raise ValueError('Deployed source/configuration changed')
    parent=directory if destination=='offline' else root/'data/ci/releases'/manifest['offline_release']
    command=['-m','feature_store.staged_job','--destination',destination,'--operation',operation,'--source-id',manifest['source_id'],
        '--state','/workspace/state','--output','/workspace/reports/'+operation+'.json']
    volumes={str(parent/'state'):{'bind':'/workspace/state','mode':'rw' if destination=='offline' else 'ro'},
             str(directory/'runs'):{'bind':'/workspace/reports','mode':'rw'}}
    if destination=='online':
        volumes[str(directory/'state')]={'bind':'/workspace/online-state','mode':'rw'}
        volumes[str(parent/'runs')]={'bind':'/workspace/upstream-reports','mode':'ro'}
        command+=['--online-state','/workspace/online-state','--offline-report','/workspace/upstream-reports/replay.json']
    client=docker.from_env();container=client.containers.run(manifest['image']['id'],command,detach=True,network='host',working_dir='/workspace',
        environment={'PYTHONPATH':'/opt/iot-release','IOT_FEAST_REDIS':manifest['redis_url'],'FEAST_USAGE':'False'},
        volumes=volumes,mem_limit='4g',memswap_limit='4g',nano_cpus=1_000_000_000,pids_limit=256,cap_drop=['ALL'],security_opt=['no-new-privileges'],
        read_only=True,tmpfs={'/tmp':'size=128m,mode=1777'},labels={'iot.ci.release':release,'iot.operation':operation,'iot.destination':destination})
    try:
        for line in container.logs(stream=True,follow=True):print(line.decode(errors='replace').rstrip(),flush=True)
        status=container.wait(timeout=300)['StatusCode'];container.reload();value=container.attrs
        receipt={'id':container.id,'image_id':value['Image'],'exit_code':status,'oom_killed':value['State']['OOMKilled'],
            'started_utc':value['State']['StartedAt'],'finished_utc':value['State']['FinishedAt'],'memory_bytes':value['HostConfig']['Memory'],
            'nano_cpus':value['HostConfig']['NanoCpus'],'memory_swap_bytes':value['HostConfig']['MemorySwap'],
            'mounts':[{'source':v['Source'],'destination':v['Destination'],'rw':v['RW']} for v in value['Mounts']],
            'commit':commit,'release':release,'destination':destination,'operation':operation}
        (directory/'runs'/(operation+'-container.json')).write_text(json.dumps(receipt,indent=2)+'\n')
        if status or receipt['oom_killed']:raise RuntimeError('Native stream-store job failed; retain its reports and journal')
    finally:
        container.reload()
        if not container.attrs['State']['Running']:container.remove()
        client.close()


for destination in ['offline','online']:
    with DAG(dag_id='iot_ci_stream_'+destination,schedule=None,catchup=False,max_active_runs=1,is_paused_upon_creation=False,
             start_date=datetime(2026,10,1,tzinfo=timezone.utc),tags=['iot','ci','stream',destination],
             default_args={'retries':0,'execution_timeout':timedelta(minutes=6)}) as dag:
        first=PythonOperator(task_id='push_finalized_windows',python_callable=execute,op_kwargs={'destination':destination,'operation':'write'},pool='feast_jobs')
        replay=PythonOperator(task_id='replay_without_new_writes',python_callable=execute,op_kwargs={'destination':destination,'operation':'replay'},pool='feast_jobs')
        first>>replay
        globals()['stream_'+destination]=dag
del dag
