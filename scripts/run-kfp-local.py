"""Run and inspect four genuine KFP Docker components on the existing Linux VM, with resource guards."""

import argparse,json,os,re,signal,sys,threading,time
from datetime import datetime,timezone
from pathlib import Path
from shutil import disk_usage

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.training_contract import digest,inventory,save
from ml.protocol import load_protocol


def run(args):
    """Keep local KFP execution distinct from a deployed Kubeflow control plane or successful UI run."""
    import docker
    from docker.types import Mount
    from kfp import compiler,local
    from ml.kfp_training import training_pipeline
    if os.name!='posix' or not re.fullmatch('[a-z][a-z0-9-]{2,39}',args.run):raise ValueError('Use Linux and a new safe run name')
    deadline=datetime.fromisoformat(args.deadline_utc.replace('Z','+00:00'))
    if deadline.tzinfo is None or (deadline-datetime.now(timezone.utc)).total_seconds()<5400:raise ValueError('At least90minutes must remain before actual VM auto STOP')
    memory=lambda:int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024
    if memory()<24*1024**3 or disk_usage('/srv/iot-fast').free<40_000_000_000 or disk_usage('/srv/iot-data').free<30_000_000_000:raise ValueError('Insufficient concurrent ML reserve')
    load_protocol(ROOT/'ml/protocols/medium-60d-v1.json')
    oracle=json.loads(args.oracles.read_text())
    if not oracle['passed'] or not all(oracle['checks'].values()) or oracle['job_code_sha256']!=digest(ROOT/'ml/training_job.py'):
        raise ValueError('Run the known-answer Spark model/time-cut oracles for these exact execution bytes first')
    fence=json.loads(args.ingest_fence.read_text())['execution_sha256'] if args.ingest_fence else {}
    if any(digest(ROOT/n)!=h for n,h in fence.items()):raise ValueError('Active ingest execution bytes changed')
    output=ROOT/'outputs/kfp-local'/args.run;workspace=ROOT/'data/ml-workspaces'/args.run;scratch=Path('/srv/iot-fast/ml-scratch')/args.run
    for path in [output,workspace,scratch]:path.mkdir(parents=True,exist_ok=False)
    # Feast owns its existing write.lock as10001; Spark's native user is185.
    # Allow only the new nonsecret workspace/artifact/scratch trees to be shared by those identities.
    for path in [workspace,scratch]:path.chmod(0o1777)
    (workspace/'training').mkdir();(workspace/'training').chmod(0o1777)
    output.chmod(0o777);(output/'artifacts').mkdir();(output/'artifacts').chmod(0o777)
    previous_umask=os.umask(0)
    api=docker.from_env();label={'iot.coursework.ml':args.run};observed={};stop=threading.Event();errors=[]
    image_ids={name:api.images.get(name).id for name in ['iot-coursework-feast:0.67.0','iot-coursework-spark:3.5.9']}
    source={p.relative_to(ROOT).as_posix():digest(p) for p in [*sorted((ROOT/'ml').glob('*.py')),ROOT/'scripts/run-kfp-local.py']}
    save(output/'preflight.json',{'checked_utc':datetime.now(timezone.utc).isoformat(),'code_sha256':source,'images':image_ids,
        'available_ram_bytes':memory(),'deadline_utc':args.deadline_utc,'full_ingest_code_unchanged':True,'oracles_sha256':digest(args.oracles),
        'scope':'Local KFP DockerRunner, four serial components, not a deployed KFP server/UI.'})
    compiler.Compiler().compile(training_pipeline,str(output/'pipeline.yaml'))

    def observe():
        """Inspect actual runtime limits and stop only this run's containers if reserves are exhausted."""
        client=docker.from_env()
        try:
            while not stop.is_set():
                for container in client.containers.list(filters={'label':'iot.coursework.ml='+args.run}):
                    state=container.attrs;limits=state['HostConfig']
                    if limits['Memory']!=10*1024**3 or limits['NanoCpus']!=2_000_000_000:raise ValueError('Actual KFP component limits differ')
                    if state['Image'] not in image_ids.values():raise ValueError('A KFP component image changed after admission')
                    observed[container.id]={'name':container.name,'image_id':state['Image'],'memory_bytes':limits['Memory'],
                        'nano_cpus':limits['NanoCpus'],'command':state['Config']['Cmd'],'user':state['Config']['User'],'running_observed':True}
                    if memory()<8*1024**3 or min(disk_usage(p).free for p in ['/srv/iot-data','/srv/iot-fast'])<20_000_000_000:
                        container.stop(timeout=15);raise ValueError('Only this ML component stopped for exhausted reserve')
                stop.wait(0.5)
        except Exception as error:errors.append(str(error))
        finally:client.close()

    mounts=[Mount('/workspace/ml',str(ROOT/'ml'),type='bind',read_only=True),
        Mount('/workspace/feature_store',str(ROOT/'feature_store'),type='bind',read_only=True),
        Mount('/workspace/data/feast',str(ROOT/'data/feast'),type='bind'),
        Mount('/workspace/data/feature-datasets',str(ROOT/'data/feature-datasets'),type='bind',read_only=True),
        Mount('/workspace/outputs',str(ROOT/'outputs'),type='bind',read_only=True),
        Mount('/ml-scratch',str(scratch),type='bind'),Mount('/ml-data',str(workspace),type='bind')]
    disposable_workspace=output/'sdk-workspace';disposable_workspace.mkdir();disposable_workspace.chmod(0o777)
    local.init(runner=local.DockerRunner(entrypoint=[],network='iot-feast_default',mounts=mounts,
        mem_limit=10*1024**3,nano_cpus=2_000_000_000,labels=label,
        environment={'HOME':'/tmp','PYTHONPATH':'/workspace','FEAST_USAGE':'False','IOT_FEAST_REDIS':'online:6379',
            'IOT_KFP_LOCAL_ARTIFACT_ROOT':str(output/'artifacts'),
            'IOT_FEAST_TEMP':'/ml-scratch/offline','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','SPARK_LOCAL_IP':'127.0.0.1'}),
        pipeline_root=str(output/'artifacts'),workspace_root=str(disposable_workspace),raise_on_error=True,enable_caching=False)
    observer=threading.Thread(target=observe,daemon=True);observer.start();started=time.monotonic()
    def terminate(*_):
        """Let the finalizer stop only this labelled run if its owning unit is terminated."""
        raise SystemExit(143)
    signal.signal(signal.SIGTERM,terminate)
    try:
        training_pipeline(workspace='/ml-data/training')
        if errors:raise ValueError(errors[0])
        descriptors={}
        for path in (output/'artifacts').rglob('*'):
            if not path.is_file():continue
            try:value=json.loads(path.read_text())
            except (ValueError,UnicodeError):continue
            if isinstance(value,dict) and value.get('kind') in ['feast-cohort','temporal-split','validation-selected-model','holdout-evaluation']:
                if value['kind'] in descriptors or not value['passed']:raise ValueError('Missing/duplicate KFP stage success binding')
                for item in value['artifacts']:
                    relative=Path(item['path']).relative_to('/ml-data')
                    if inventory(workspace/relative)!=item['files']:
                        raise ValueError('Persistent ML artifacts disappeared or changed after local KFP cleanup')
                descriptors[value['kind']]={'path':path.relative_to(output).as_posix(),'sha256':digest(path)}
        if len(descriptors)!=4 or len(observed)!=4:raise ValueError('Require all four real observed components and sealed output artifacts')
        if any(digest(ROOT/n)!=h for n,h in {**fence,**source}.items()):raise ValueError('ML/full-ingest execution bytes changed during pipeline')
        save(output/'verification.json',{'passed':True,'preflight_sha256':digest(output/'preflight.json'),
            'pipeline_yaml_sha256':digest(output/'pipeline.yaml'),'components':list(observed.values()),'stage_artifacts':descriptors,
            'seconds':time.monotonic()-started,'full_ingest_code_unchanged':True,
            'scope':'Four genuine KFP DockerRunner components and full native cohort/actual model evaluation. Not a deployed Kubeflow cluster/UI or100GB ML.'})
        print(json.dumps({'passed':True,'components':4,'output':str(output)}),flush=True)
    finally:
        stop.set();observer.join(timeout=5)
        save(output/'observed-containers.json',list(observed.values()))
        for container in api.containers.list(filters={'label':'iot.coursework.ml='+args.run}):container.stop(timeout=15)
        api.close();os.umask(previous_umask)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True);parser.add_argument('--deadline-utc',required=True)
    parser.add_argument('--oracles',type=Path,required=True)
    parser.add_argument('--ingest-fence',type=Path);run(parser.parse_args())
