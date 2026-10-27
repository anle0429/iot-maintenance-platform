"""Run a tiny actual Kubernetes Spark oracle before another expensive native Feast pipeline submission."""
import argparse,json,os,re,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from shutil import disk_usage
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.training_contract import digest,save
from ml.distributed_evidence import read_logs,summarize


def run(args):
    """Preserve a failed job's real logs and require both known answers and two-executor LR work."""
    import yaml
    deadline=datetime.fromisoformat(args.deadline_utc.replace('Z','+00:00'))
    if not re.fullmatch('[a-z][a-z0-9-]{2,39}',args.run) or deadline.tzinfo is None:raise ValueError('Use safe run and actual deadline')
    ram=lambda:int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
    if (deadline-datetime.now(timezone.utc)).total_seconds()<2400 or ram()<24*1024**3 or disk_usage('/srv/iot-fast').free<30_000_000_000:
        raise ValueError('Insufficient oracle reserves')
    out=ROOT/'outputs/distributed-oracle'/args.run;out.mkdir(parents=True,exist_ok=False)
    workspace=ROOT/'data/ml-workspaces/training-cluster-27'/args.run;workspace.mkdir(exist_ok=False);os.chown(workspace,0,10001);workspace.chmod(0o2775)
    sources={n:digest(ROOT/n) for n in ['ml/distributed_submit.py','ml/distributed_oracle.py','ml/distributed_job.py','ml/distributed_evidence.py',
        'ml/training_oracles.py','ml/training_job.py','ml/cluster_training_job.py','ml/training_contract.py','infra/kfp/spark-executor.yaml','infra/kfp/spark-oracle.yaml']}
    save(out/'preflight.json',{'checked_utc':datetime.now(timezone.utc).isoformat(),'code_sha256':sources,'deadline_utc':args.deadline_utc,'scope':'Actual360-row distributed scheduler/algorithm oracle, not real IoT quality.'})
    job=yaml.safe_load((ROOT/'infra/kfp/spark-oracle.yaml').read_text().replace('__RUN__',args.run));save(out/'job-submitted.json',job)
    k=['/usr/local/bin/k3s','kubectl'];subprocess.run(k+['apply','-f',str(out/'job-submitted.json')],check=True)
    started=time.monotonic();failure=None
    try:
        while True:
            status=json.loads(subprocess.check_output(k+['get','job',args.run,'-n','kubeflow','-o','json'],text=True))
            (out/'job.json').write_text(json.dumps(status,indent=2)+'\n')
            executors=json.loads(subprocess.check_output(k+['get','pods','-n','kubeflow','-l','spark-role=executor,iot-coursework-run='+args.run,'-o','json'],text=True))
            (out/'executor-pods.json').write_text(json.dumps(executors,indent=2)+'\n')
            if sum(p.get('status',{}).get('phase')=='Running' for p in executors['items'])>=2 and not (out/'two-executors-running.json').exists():save(out/'two-executors-running.json',executors)
            if status.get('status',{}).get('succeeded'):break
            if status.get('status',{}).get('failed'):raise ValueError('Actual Kubernetes oracle job failed')
            if time.monotonic()-started>1200 or ram()<8*1024**3 or (deadline-datetime.now(timezone.utc)).total_seconds()<600:raise ValueError('Oracle reserve/deadline guard')
            time.sleep(5)
        original=json.loads((workspace/'oracle-verification.json').read_text());save(out/'algorithm-verification.json',original)
        distribution=summarize(read_logs([p for p in (workspace/'spark-events').iterdir() if p.is_file() and not p.name.startswith('.')]))
        save(out/'distribution.json',distribution)
        if not original['passed'] or not all(original['checks'].values()) or not distribution['passed'] or not (out/'two-executors-running.json').exists():
            raise ValueError('Known-answer/distribution proof failed despite job completion')
        if any(digest(ROOT/n)!=h for n,h in sources.items()):raise ValueError('Oracle execution code changed')
        save(out/'verification.json',{'passed':True,'run':args.run,'completed_utc':datetime.now(timezone.utc).isoformat(),'seconds':time.monotonic()-started,
            'code_sha256':sources,'algorithm_receipt_sha256':digest(out/'algorithm-verification.json'),'distribution_receipt_sha256':digest(out/'distribution.json'),
            'scope':'Actual two-executor360-row known-answer algorithm and scheduler proof on one physical VM. No native Feast/cohort or speedup claim.'})
    except BaseException as error:
        failure=error;save(out/'failure.json',{'passed':False,'type':type(error).__name__,'message':str(error)});raise
    finally:
        pods=json.loads(subprocess.check_output(k+['get','pods','-n','kubeflow','-l','job-name='+args.run,'-o','json'],text=True));save(out/'driver-pods.json',pods)
        for p in pods['items']:
            with (out/(p['metadata']['name']+'-driver.txt')).open('w') as stream:subprocess.run(k+['logs','-n','kubeflow',p['metadata']['name'],'-c','driver'],stdout=stream,stderr=subprocess.STDOUT)
        if failure:
            subprocess.run(k+['delete','job',args.run,'-n','kubeflow','--cascade=foreground','--wait=false'],capture_output=True)
            subprocess.run(k+['delete','pods','-n','kubeflow','-l','spark-role=executor,iot-coursework-run='+args.run,'--wait=false'],capture_output=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True);parser.add_argument('--deadline-utc',required=True);run(parser.parse_args())
