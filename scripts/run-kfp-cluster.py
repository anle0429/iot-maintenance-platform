"""Submit the typed-artifact oracle and four genuine training tasks to the private KFP backend with resource/time guards."""
from pathlib import Path
from datetime import datetime,timezone
from hashlib import sha256
from shutil import disk_usage
import argparse,json,os,re,subprocess,time


def run(args):
    """Preserve completed runs and stop only this submission if the VM reserve is exhausted."""
    import kfp
    root=Path(__file__).resolve().parents[1]
    if os.name!='posix' or not re.fullmatch('[a-z][a-z0-9-]{2,45}',args.run):raise ValueError('Use a new safe Linux run name')
    deadline=datetime.fromisoformat(args.deadline_utc.replace('Z','+00:00'))
    if deadline.tzinfo is None or (deadline-datetime.now(timezone.utc)).total_seconds()<2400:raise ValueError('At least40minutes must remain before VM auto STOP')
    ram=lambda:int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
    if ram()<24*1024**3 or disk_usage('/srv/iot-fast').free<40*1024**3:raise ValueError('Insufficient cluster training reserve')
    prepared=root/args.prepared;v=json.loads((prepared/'verification.json').read_text())
    if not v['passed'] or any(sha256((root/n).read_bytes()).hexdigest()!=h for n,h in v['new_code_sha256'].items()):raise ValueError('Cluster adapter changed after preparation')
    out=root/'outputs/kfp-cluster'/args.run;out.mkdir(parents=True,exist_ok=False)
    workspace=root/'data/ml-workspaces/training-cluster-27'/args.run;workspace.mkdir(exist_ok=False);os.chown(workspace,0,10001);workspace.chmod(0o2775)
    k=['/usr/local/bin/k3s','kubectl']
    service=json.loads(subprocess.check_output(k+['get','service','ml-pipeline','-n','kubeflow','-o','json'],text=True))
    client=kfp.Client(host='http://'+service['spec']['clusterIP']+':8888',namespace='kubeflow')
    def save(name,value):(out/name).write_text(json.dumps(value,indent=2,default=str)+'\n')
    sources={p.relative_to(root).as_posix():sha256(p.read_bytes()).hexdigest() for p in [*sorted((root/'ml').glob('*.py')),Path(__file__)]}
    save('preflight.json',{'checked_utc':datetime.now(timezone.utc).isoformat(),'code_sha256':sources,'prepared_verification_sha256':sha256((prepared/'verification.json').read_bytes()).hexdigest(),'sdk_version':kfp.__version__,'deadline_utc':args.deadline_utc,'workspace':str(workspace),'ram_available_bytes':ram(),'scope':'Real standalone backend submission, frozen protocol reproduction, not a new protocol/model selection.'})
    active=None;started=time.monotonic()
    try:
        runs={}
        for stage,file in [('oracle','oracle-pipeline.yaml'),('training','training-pipeline.yaml')]:
            if stage=='training' and (deadline-datetime.now(timezone.utc)).total_seconds()<2100:raise ValueError('Insufficient remaining session for full native cohort')
            result=client.create_run_from_pipeline_package(str(prepared/file),arguments={} if stage=='oracle' else {'workspace':'/ml-data/'+args.run},
                run_name=args.run+'-'+stage,experiment_name='iot-cluster-training',namespace='kubeflow',enable_caching=False)
            active=result.run_id;runs[stage]=active;save('submitted-runs.json',runs)
            while True:
                details=client.get_run(active).to_dict();save(stage+'-run.json',details)
                state=str(details.get('state','')).upper();save('progress.json',{'stage':stage,'run_id':active,'state':state,'checked_utc':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':time.monotonic()-started})
                if state in ['SUCCEEDED','FAILED','CANCELED','CANCELLED','SKIPPED']:
                    if state!='SUCCEEDED':raise ValueError(stage+' actual backend run ended '+state)
                    break
                if ram()<8*1024**3 or min(disk_usage('/srv/iot-data').free,disk_usage('/srv/iot-fast').free)<20_000_000_000 or (deadline-datetime.now(timezone.utc)).total_seconds()<600:
                    client.terminate_run(active);raise ValueError('Only this run terminated for reserve/VMdeadline guard')
                time.sleep(10)
            pods=json.loads(subprocess.check_output(k+['get','pods','-n','kubeflow','-l','pipelines.kubeflow.org/runid='+active,'-o','json'],text=True));save(stage+'-pods.json',pods)
            active=None
        if any(sha256((root/n).read_bytes()).hexdigest()!=h for n,h in sources.items()):raise ValueError('Execution bytes changed during actual cluster run')
        cohort=json.loads((workspace/'feast-load/report.json').read_text())
        if not cohort['passed'] or cohort['native_rows_this_invocation']!=3_132_184 or cohort['total_rows']!=3_132_184:raise ValueError('Cluster run did not retrieve the complete native Feast cohort')
        save('verification.json',{'passed':True,'completed_utc':datetime.now(timezone.utc).isoformat(),'run_ids':runs,'seconds':time.monotonic()-started,'native_feast_rows':cohort['total_rows'],'source_preflight_sha256':sha256((out/'preflight.json').read_bytes()).hexdigest(),'scope':'Two-task known-answer transport and four real training tasks succeeded on actual KFP backend. Protocol frozen; evaluation is a reproduction, no holdout-driven tuning.'})
    except BaseException as error:
        save('failure.json',{'passed':False,'type':type(error).__name__,'message':str(error),'active_run_id':active,'scope':'Actual failed/partial submission retained; no automatic resubmission or overwrite.'});raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True);parser.add_argument('--deadline-utc',required=True)
    parser.add_argument('--prepared',type=Path,default=Path('outputs/kfp-training-prepare-27'))
    run(parser.parse_args())
