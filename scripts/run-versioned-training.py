"""Run five real KFP ML stages with mandatory incremental data publication and candidate model registration."""
import argparse,json,os,re,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from shutil import disk_usage,copyfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.training_contract import digest,save
from ml.automatic_versions import context
from ml.distributed_evidence import read_logs,summarize


def progress(path,value):
    """Replace only live observation snapshots; completed receipts still use the no-overwrite writer."""
    pending=path.with_suffix('.pending');pending.write_text(json.dumps(value,indent=2,default=str)+'\n');pending.replace(path)


def run(args):
    """Keep prior resource/oracle gates and require all automatic persistence receipts before declaring success."""
    import kfp
    from kfp import compiler
    from ml.kfp_versioned import versioned_pipeline
    deadline=datetime.fromisoformat(args.deadline_utc.replace('Z','+00:00'))
    if os.name!='posix' or not re.fullmatch('[a-z][a-z0-9-]{2,39}',args.run):raise ValueError('Use a safe fresh Linux run name')
    ram=lambda:int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
    if deadline.tzinfo is None or (deadline-datetime.now(timezone.utc)).total_seconds()<5400:raise ValueError('Need90minutes before actual VM auto STOP')
    if ram()<24*1024**3 or min(disk_usage(p).free for p in ['/srv/iot-data','/srv/iot-fast'])<40_000_000_000:raise ValueError('Insufficient distributed ML reserves')
    binding=context(args.source_binding)
    pod_binding='/workspace/'+args.source_binding.resolve().relative_to(ROOT).as_posix()
    oracle=json.loads((ROOT/args.oracle).read_text())
    if not oracle['passed'] or any(digest(ROOT/n)!=h for n,h in oracle['code_sha256'].items()):raise ValueError('Require the actual distributed oracle for these unchanged kernels')
    out=ROOT/'outputs/versioned-training'/args.run;out.mkdir(parents=True,exist_ok=False)
    workspace=ROOT/'data/ml-workspaces/training-cluster-27'/args.run;workspace.mkdir(exist_ok=False);os.chown(workspace,0,10001);workspace.chmod(0o2775)
    save(out/'preflight.json',{'checked_utc':datetime.now(timezone.utc).isoformat(),'code_sha256':binding['files'],
        'source_snapshot_commit':binding['commit'],'deadline_utc':args.deadline_utc,'ram_available_bytes':ram(),
        'sdk_version':kfp.__version__,'oracle_sha256':digest(ROOT/args.oracle),'source_binding_sha256':digest(args.source_binding),
        'scope':'Admission only. Five tasks, successful native persistence and two working executors still required.'})
    package=out/'pipeline.yaml'
    deployment=None
    if getattr(args,'deployment_manifest',None):
        deployment=json.loads(args.deployment_manifest.read_text())
        if (not deployment['passed'] or deployment['commit']!=binding['commit']
                or deployment['source_sha256']!=binding['files']
                or digest(Path(deployment['pipeline_path']))!=deployment['pipeline_sha256']):
            raise ValueError('CI package differs from its tested and deployed source')
        copyfile(deployment['pipeline_path'],package)
        save(out/'ci-deployment.json',{'manifest_sha256':digest(args.deployment_manifest),
            'pipeline_sha256':digest(package),'commit':binding['commit'],'images':deployment['images']})
    else:
        compiler.Compiler().compile(versioned_pipeline,str(package))
    k=['/usr/local/bin/k3s','kubectl'];service=json.loads(subprocess.check_output(k+['get','service','ml-pipeline','-n','kubeflow','-o','json'],text=True))
    client=kfp.Client(host='http://'+service['spec']['clusterIP']+':8888',namespace='kubeflow')
    result=client.create_run_from_pipeline_package(str(package),arguments={'workspace':'/ml-data/'+args.run,'source_binding':pod_binding},
        run_name=args.run,experiment_name='iot-versioned-distributed',namespace='kubeflow',enable_caching=False)
    run_id=result.run_id;save(out/'submission.json',{'run_id':run_id});started=time.monotonic()
    try:
        while True:
            details=client.get_run(run_id).to_dict();progress(out/'run.json',details)
            state=str(details.get('state','')).upper();progress(out/'progress.json',{'run_id':run_id,'state':state,'elapsed_seconds':time.monotonic()-started,'checked_utc':datetime.now(timezone.utc).isoformat()})
            pods=json.loads(subprocess.check_output(k+['get','pods','-n','kubeflow','-l','iot-coursework-run='+args.run,'-o','json'],text=True))
            if pods['items']:
                progress(out/'executor-pods.json',pods)
                if sum(p.get('status',{}).get('phase')=='Running' for p in pods['items'])>=2 and not (out/'two-executors-running.json').exists():save(out/'two-executors-running.json',pods)
            if state in ['SUCCEEDED','FAILED','CANCELED','CANCELLED','SKIPPED']:
                if state!='SUCCEEDED':raise ValueError('Actual KFP run ended '+state)
                break
            if ram()<8*1024**3 or min(disk_usage(p).free for p in ['/srv/iot-data','/srv/iot-fast'])<20_000_000_000 or (deadline-datetime.now(timezone.utc)).total_seconds()<600:
                client.terminate_run(run_id);raise ValueError('Only this KFP run terminated for reserve guard')
            time.sleep(10)
        logs=[p for p in (workspace/'spark-events').iterdir() if p.is_file() and not p.name.startswith('.')]
        distribution=summarize(read_logs(logs));save(out/'distributed-verification.json',{**distribution,'event_logs':{p.name:digest(p) for p in logs}})
        data=json.loads((workspace/'lineage/automatic-data.json').read_text());model=json.loads((workspace/'lineage/registration.json').read_text())
        if not distribution['passed'] or not (out/'two-executors-running.json').exists() or not data['passed'] or not model['passed'] or model['production']:
            raise ValueError('Backend success lacks complete native distributed/data/model evidence')
        if data['data_version']!=model['incremental_data_reference']['data_version'] or data['data_run_id']!=model['incremental_data_reference']['data_run_id'] or model['source_snapshot_commit']!=binding['commit']:
            raise ValueError('Model and automatic pull point to different source/data versions')
        if any(digest(ROOT/n)!=h for n,h in binding['files'].items()):raise ValueError('Execution source changed during run')
        save(out/'pipeline-pods.json',json.loads(subprocess.check_output(k+['get','pods','-n','kubeflow','-l','pipeline/runid='+run_id,'-o','json'],text=True)))
        save(out/'verification.json',{'passed':True,'run_id':run_id,'completed_utc':datetime.now(timezone.utc).isoformat(),'seconds':time.monotonic()-started,
            'distributed_proof_sha256':digest(out/'distributed-verification.json'),'source_preflight_sha256':digest(out/'preflight.json'),
            'data_version':data['data_version'],'data_run_id':data['data_run_id'],'new_remote_blob_payload_bytes':data['new_remote_blob_payload_bytes'],
            'registered_model_version':model['version'],'model_run_id':model['model_run_id'],'production':False,
            'scope':'Five real KFP stages; automatic incremental version on this fresh pull and model-only candidate registration after evaluation. Two native executors on one physical VM; no production promotion.'})
    except BaseException as error:
        save(out/'failure.json',{'passed':False,'run_id':run_id,'type':type(error).__name__,'message':str(error)});raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for n in ['run','deadline-utc']:parser.add_argument('--'+n,required=True)
    parser.add_argument('--source-binding',type=Path,required=True);parser.add_argument('--oracle',type=Path,required=True)
    parser.add_argument('--deployment-manifest',type=Path)
    run(parser.parse_args())
