"""Test, build, auto-deploy and verify an isolated native Feast materialization release in Jenkins."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from feature_store.ci_contract import identity, verify_sequence

DAG = 'iot_ci_materialization'


def digest(path):
    """Hash small executable source and evidence files."""
    return sha256(path.read_bytes()).hexdigest()


def save(path,value):
    """Keep native JSON atomic and omit in-memory credentials."""
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.pending')
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)


def api(path,token=None,body=None):
    """Call the private Airflow API without persisting auth responses or error bodies."""
    headers={'Content-Type':'application/json'}
    if token:
        headers['Authorization']='Bearer '+token
    try:
        with urlopen(Request(os.environ['IOT_AIRFLOW_URL']+path,headers=headers,
                data=json.dumps(body).encode() if body is not None else None),timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError('Private Airflow HTTP '+str(error.code)) from None


def run(phase):
    """Refuse changed source and require real native tasks and all200 online vectors before CI success."""
    release,project=identity(os.environ['BUILD_NUMBER'])
    commit=os.environ['IOT_CI_COMMIT']
    if os.environ.get('IOT_CI_PROVIDER')!='jenkins' or not os.environ.get('JENKINS_URL'):
        raise ValueError('Only the authenticated private Jenkins job may deploy')
    deploy=Path(os.environ['IOT_DEPLOY_ROOT']).resolve()
    seal_path=deploy/'data/ci/source'/commit/'manifest.json'
    seal=json.loads(seal_path.read_text())
    if seal['commit']!=commit:
        raise ValueError('Source commit differs from its seal')
    for name,expected in seal['files'].items():
        path=(ROOT/name).resolve()
        if not path.is_relative_to(ROOT) or digest(path)!=expected:
            raise ValueError('Tested source changed: '+name)
    directory=deploy/'data/ci/releases'/release
    out=ROOT/'outputs/ci-materialization'
    out.mkdir(parents=True,exist_ok=True)
    if phase=='test':
        subprocess.run(['python3','-m','unittest','discover','-s','tests','-p','test_ci_materialization.py','-v'],cwd=ROOT,check=True)
        for name in seal['files']:
            if name.endswith('.py'):
                compile((ROOT/name).read_text(),name,'exec')
        save(out/'test.json',{'passed':True,'commit':commit,'source_seal_sha256':digest(seal_path),
            'tests':'Known-answer identity, source binding, vector equality and replay interval regressions'})
    elif phase=='build':
        if not json.loads((out/'test.json').read_text())['passed']:
            raise ValueError('Test stage must pass before build')
        stop=datetime.fromisoformat(json.loads((deploy/'data/runtime/ci/session.json').read_text())['auto_stop_utc'])
        ram=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024
        free=min(shutil.disk_usage(p).free for p in [deploy,Path('/srv/iot-fast/ci')])
        remaining=(stop-datetime.now(timezone.utc)).total_seconds()
        admission={'ram_available_bytes':ram,'minimum_disk_free_bytes':free,'seconds_before_actual_stop':remaining,
            'required_seconds':1500,'required_ram_bytes':12*1024**3,'required_disk_bytes':10_000_000_000,
            'checked_utc':datetime.now(timezone.utc).isoformat(),
            'scope':'Bounded existing60day compact-source materialization only; no generator/Spark/training or40minute DP CI guard change.'}
        save(out/'admission.json',admission)
        if remaining<1500 or ram<12*1024**3 or free<10_000_000_000:
            raise ValueError('Materialization CI needs25minutes,12GiB RAM and10GB per disk')
        directory.mkdir(parents=True,exist_ok=False)
        for name in ['state','outputs']:
            (directory/name).mkdir();(directory/name).chmod(0o1777)
        images={}
        sources={n:h for n,h in seal['files'].items() if n.startswith('feature_store/') or n in
            ['ml/__init__.py','ml/protocol.py','ml/protocols/medium-60d-v1.json']}
        for kind,runtime in [('feast','iot-coursework-feast:0.67.0'),('metadata','iot-coursework-metadata:1.6.0')]:
            base=subprocess.check_output(['docker','image','inspect',runtime,'--format','{{.Id}}'],text=True).strip()
            tag='iot-ci-materialization-'+kind+':'+release
            subprocess.run(['docker','build','--build-arg','RUNTIME='+runtime,'--label','org.opencontainers.image.revision='+commit,
                '-f',str(ROOT/'infra/ci/Materialization.Dockerfile'),'-t',tag,str(ROOT)],check=True)
            image=json.loads(subprocess.check_output(['docker','image','inspect',tag],text=True))[0]
            probe="import pathlib,hashlib,json; r=pathlib.Path('/opt/iot-release'); print(json.dumps({str(p.relative_to(r)):hashlib.sha256(p.read_bytes()).hexdigest() for p in r.rglob('*') if p.is_file() and '__pycache__' not in p.parts}))"
            packaged=json.loads(subprocess.check_output(['docker','run','--rm','--network','none','--memory','256m','--cpus','.5','--entrypoint','python3',image['Id'],'-c',probe],text=True))
            if packaged!=sources or image['Config']['Labels']['org.opencontainers.image.revision']!=commit:
                raise ValueError('Packaged source differs from tested source')
            images[kind]={'id':image['Id'],'tag':tag,'runtime_base_id':base,'packaged_source_verified':True}
        manifest={'release':release,'commit':commit,'project':project,'images':images,'source_sha256':sources,
            'dag_sha256':digest(ROOT/'infra/ci/iot_ci_materialization.py'),'built_utc':datetime.now(timezone.utc).isoformat()}
        save(directory/'release.json',manifest);save(out/'build.json',manifest)
    elif phase=='deploy':
        manifest=json.loads((directory/'release.json').read_text())
        target=deploy/'airflow/dags/ci/iot_ci_materialization.py'
        pending=target.with_suffix('.pending');shutil.copyfile(ROOT/'infra/ci/iot_ci_materialization.py',pending);pending.replace(target)
        if digest(target)!=manifest['dag_sha256']:
            raise ValueError('Deployed DAG readback differs')
        save(out/'deployment.json',{'passed':True,'dag':DAG,'release':release,'commit':commit,
            'dag_sha256':digest(target),'deployed_utc':datetime.now(timezone.utc).isoformat()})
    elif phase=='cleanup':
        ids=subprocess.check_output(['docker','ps','-q','--filter','label=iot.ci.release='+release],text=True).split()
        if ids:
            subprocess.run(['docker','stop','--time','20',*ids],check=True)
        save(out/'cleanup.json',{'release':release,'stopped_only_owned_containers':ids})
    else:
        token=api('/auth/token',body={'username':os.environ['IOT_AIRFLOW_USER'],'password':os.environ['IOT_AIRFLOW_PASSWORD']})['access_token']
        deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            if DAG in {x['dag_id'] for x in api('/api/v2/dags?limit=100',token)['dags']}:
                break
            time.sleep(5)
        else:
            raise RuntimeError('Auto-deployed materialization DAG has not imported')
        api('/api/v2/dags/'+DAG+'/dagRuns',token,{'dag_run_id':release,
            'logical_date':datetime.now(timezone.utc).isoformat(),'conf':{'release':release,'commit':commit}})
        deadline=time.monotonic()+720
        while time.monotonic()<deadline:
            base='/api/v2/dags/'+DAG+'/dagRuns/'+release
            state={'run':api(base,token),'tasks':api(base+'/taskInstances',token)}
            save(out/'airflow.json',state)
            if state['run']['state'] in ['success','failed']:
                if state['run']['state']!='success' or len(state['tasks']['task_instances'])!=8 or any(x['state']!='success' for x in state['tasks']['task_instances']):
                    raise RuntimeError('Native materialization CI DAG failed; inspect retained task logs')
                break
            time.sleep(5)
        else:
            raise RuntimeError('Native DAG remains running; inspect the same run before any retry')
        reports={n:json.loads((directory/'outputs'/n/'materialization.json').read_text()) for n in ['initial','incremental','replay']}
        proof=verify_sequence(reports,project)
        for name in ['initial','incremental','replay']:
            validation=json.loads((directory/'outputs'/name/'validation.json').read_text())
            if not validation['passed'] or not all(validation['checks'].values()):
                raise ValueError('Native online validation failed')
        publication=json.loads((directory/'outputs/incremental/datahub-publication.json').read_text())
        if publication['run']!=release or publication['job']!='urn:li:dataJob:(urn:li:dataFlow:(airflow,iot_ci_materialization,iot-vm),incremental_materialize)':
            raise ValueError('Published producing job differs from the actual CI task')
        query='''query($urn:String!) { dataset(urn:$urn) { urn name lineage(input:{direction:UPSTREAM,start:0,count:100}) { relationships { entity { urn type } } } assertions(start:0,count:100) { assertions { urn runEvents(limit:20) { runEvents { runId result { type } } } } } } }'''
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            request=Request('http://datahub-gms:8080/api/graphql',headers={'Content-Type':'application/json'},
                data=json.dumps({'query':query,'variables':{'urn':publication['output']}}).encode())
            with urlopen(request,timeout=20) as response:
                indexed=json.load(response)
            dataset=(indexed.get('data') or {}).get('dataset')
            if not indexed.get('errors') and dataset:
                upstream={x['entity']['urn'] for x in dataset['lineage']['relationships'] if x['entity']}
                definitions={x['urn']:x for x in dataset['assertions']['assertions']}
                if {publication['input'],publication['job']}<=upstream and all(a['urn'] in definitions and any(
                        e['runId']==release and e['result']['type']=='SUCCESS' for e in definitions[a['urn']]['runEvents']['runEvents'])
                        for a in publication['assertions']):
                    save(out/'datahub-indexed.json',{'passed':True,'run':release,'dataset':dataset,'checked_utc':datetime.now(timezone.utc).isoformat()})
                    break
            time.sleep(5)
        else:
            raise ValueError('CI output or six assertion histories are not indexed yet')
        for path in (directory/'outputs').rglob('*.json'):
            relative=path.relative_to(directory/'outputs');target=out/'native'/relative
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        save(out/'verification.json',{**proof,'commit':commit,'release':release,'project':project,
            'native_tasks':8,'completed_utc':datetime.now(timezone.utc).isoformat(),
            'scope':'Jenkins tests/build/automatic deployment of eight-task native Feast pipeline: isolated initial/incremental/replay and DataHub publication. Existing medium source, no training/100GB claim.'})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['test','build','deploy','verify','cleanup'])
    run(parser.parse_args().phase)
