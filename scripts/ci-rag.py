"""Test, build, deploy and verify a source-sealed RAG application through the existing private Jenkins and Airflow lab."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener
from urllib.error import HTTPError
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agents.ci_contract import release_identity, require_receipt, validate_source
from ml.serving_ci import host_source_path
SPEC=importlib.util.spec_from_file_location('rag_ci_shared',ROOT/'scripts/ci-serving.py')
SHARED=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(SHARED)
DAG_ID='iot_ci_rag_knowledge'
PACKAGED=sorted([p.relative_to(ROOT).as_posix() for p in (ROOT/'rag').glob('*.py')]
                +[p.relative_to(ROOT).as_posix() for p in (ROOT/'knowledge').glob('*') if p.is_file()]
                +['infra/rag/embedding.lock.json'])


class Context(SHARED.Context):
    """Reuse bounded native transport while giving RAG its own source format, release and outputs."""
    driver='/src/scripts/ci-rag.py'

    def __init__(self):
        """Bind this one trusted RAG job to its declared source and Jenkins build."""
        if (os.environ.get('IOT_CI_PROVIDER')!='jenkins' or not os.environ.get('JENKINS_URL')
                or os.environ.get('JOB_BASE_NAME')!='iot-llm-rag'):
            raise ValueError('Use the declared private Jenkins RAG job')
        self.commit=os.environ['IOT_CI_COMMIT'];self.version='6'
        self.identity=release_identity('rag',os.environ['BUILD_NUMBER'],self.commit);self.release=self.identity['release']
        self.deploy=Path(os.environ['IOT_DEPLOY_ROOT']).resolve();self.directory=self.deploy/'data/ci/releases'/self.release
        self.out=self.directory/'outputs';self.host_root=host_source_path(ROOT,self.deploy)
        self.seal=json.loads((self.deploy/'data/ci/source'/self.commit/'manifest.json').read_bytes())
        self.source_check();self.run_id='rag-ci-'+self.commit[:8]+'-'+self.identity['build_number']

    def source_check(self):
        """Authenticate all source inputs again in every Jenkins/controller phase."""
        self.source_digest=validate_source(ROOT,self.seal,self.commit)

    def phase(self,name,details):
        """Wrap native facts with their exact job/build/source identity."""
        SHARED.save(self.out/(name+'.json'),{'schema_version':1,'phase':name,'passed':True,**self.identity,**details})

    def prior(self,name):
        """Require an earlier successful phase from this exact release."""
        return require_receipt(json.loads((self.out/(name+'.json')).read_bytes()),self.identity,name)

    def native(self,phase,extra=None):
        """Run trusted control code with source read-only and explicit mounts for its new versioned artifacts."""
        forwarded=['--env','JOB_BASE_NAME','--mount','type=bind,src=/srv/iot-fast/ci,dst=/srv/iot-fast/ci,readonly']
        self.control(phase,extra=forwarded+(extra or []),memory='2g')


def image_files(image):
    """Read the exact application/corpus bytes from the built non-root image without network access."""
    code='import hashlib,json,os,pathlib;assert os.getuid()==10001;root=pathlib.Path("/opt/iot-rag-release");print(json.dumps({n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in '+repr(PACKAGED)+'}))'
    raw=subprocess.check_output(['docker','run','--rm','--network','none','--read-only','--memory','256m','--cpus','.5',
                                  '--entrypoint','python',image,'-B','-c',code],timeout=90)
    return json.loads(raw)


def airflow(path,token=None,body=None,method=None):
    """Use Jenkins-bound credentials only in memory on the existing host-private Airflow API."""
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    request=Request('http://127.0.0.1:18480'+path,headers=headers,
                    data=json.dumps(body).encode() if body is not None else None,method=method)
    try:
        with build_opener(ProxyHandler({})).open(request,timeout=30) as response:return json.load(response)
    except HTTPError as error:raise RuntimeError('Private Airflow HTTP '+str(error.code)) from None


def deploy_host(c):
    """Publish a fresh run manifest and dedicated CI DAG, preserving the existing manual DAG and all older outputs."""
    build=c.prior('build');source=(c.deploy/'data/ci/source'/c.commit/'tree').resolve()
    if not source.is_relative_to(c.deploy/'data/llm-source'):
        raise ValueError('The trusted operator must stage the source beneath data/llm-source')
    if any(sha256((source/name).read_bytes()).hexdigest()!=digest for name,digest in c.seal['files'].items()):
        raise ValueError('Prepared Airflow source differs from the CI-tested source')
    state=c.deploy/'data/rag'/c.run_id;reports=c.deploy/'outputs'/c.run_id
    manifest_path=c.deploy/'data/rag-releases'/(c.run_id+'.json')
    if any(p.exists() for p in [state,reports,manifest_path]):raise ValueError('Preserve the earlier RAG run; inspect its actual state')
    # The SDK remains in the metadata image; only its existing private bridge and immutable image ID are selected.
    gms=json.loads(subprocess.check_output(['docker','inspect','iot-datahub-datahub-gms-1']))[0]
    matches=[(name,value) for name,value in gms['NetworkSettings']['Networks'].items()
             if name.startswith('iot-metadata') and 'datahub-gms' in (value.get('Aliases') or [])]
    if len(matches)!=1:raise ValueError('Require the existing metadata bridge/DNS identity')
    network_name,network=matches[0]
    metadata=json.loads(subprocess.check_output(['docker','image','inspect','iot-coursework-metadata:1.6.0']))[0]
    inspected=json.loads(subprocess.check_output(['docker','image','inspect',build['image_id']]))[0]
    if inspected['Id']!=build['image_id']:raise ValueError('RAG application image changed after build')
    state.mkdir(parents=True);os.chown(state,10001,0);state.chmod(0o750)
    reports.mkdir(parents=True);os.chown(reports,50000,0);reports.chmod(0o750)
    manifest={'schema_version':1,'run_id':c.run_id,'source_directory':source.relative_to(c.deploy).as_posix(),
              'source_files':c.seal['files'],'metadata_network_id':network['NetworkID'],
              'image_id':build['image_id'],'metadata_image_id':metadata['Id'],
              'ci_source_commit':c.commit,'ci_release':c.release}
    manifest_path.parent.mkdir(exist_ok=True)
    with manifest_path.open('x') as stream:json.dump(manifest,stream,indent=2)
    manifest_path.chmod(0o640)
    target=c.deploy/'airflow/dags/ci/iot_ci_rag.py';target.parent.mkdir(parents=True,exist_ok=True)
    source_dag=ROOT/'infra/ci/iot_ci_rag.py'
    previous=sha256(target.read_bytes()).hexdigest() if target.is_file() else None
    pending=target.with_suffix('.pending');shutil.copyfile(source_dag,pending);pending.replace(target)
    actual=sha256(target.read_bytes()).hexdigest()
    if actual!=c.seal['files']['infra/ci/iot_ci_rag.py']:raise ValueError('The deployed DAG differs from tested source')
    c.phase('deploy',{'dag_id':DAG_ID,'run_id':c.run_id,'manifest':manifest,'dag_sha256':actual,
                      'dag_changed':previous!=actual,'metadata_network_name':network_name,
                      'deployed_utc':datetime.now(timezone.utc).isoformat()})


def verify_host(c):
    """Wait on the same actual Airflow run, require six successes and reconcile native Feast/quality/GMS outputs."""
    deployed=c.prior('deploy')
    token=airflow('/auth/token',body={'username':os.environ['IOT_AIRFLOW_USER'],'password':os.environ['IOT_AIRFLOW_PASSWORD']})['access_token']
    deadline=time.monotonic()+180
    while True:
        dags=airflow('/api/v2/dags?limit=100',token)['dags'];dag=next((d for d in dags if d['dag_id']==DAG_ID),None)
        if dag:break
        if time.monotonic()>=deadline:raise TimeoutError('The deployed CI RAG DAG has not imported')
        time.sleep(5)
    active=airflow('/api/v2/dags/'+DAG_ID+'/dagRuns?limit=100',token)['dag_runs']
    if any(r['state'] in ['queued','running'] for r in active):raise ValueError('Observe the preceding actual RAG run before starting another')
    if dag['is_paused']:airflow('/api/v2/dags/'+DAG_ID,token,{'is_paused':False},'PATCH')
    created=airflow('/api/v2/dags/'+DAG_ID+'/dagRuns',token,
                    {'dag_run_id':c.run_id,'logical_date':datetime.now(timezone.utc).isoformat(),'conf':{'run_id':c.run_id}})
    SHARED.save(c.out/'airflow-created.json',created);path='/api/v2/dags/'+DAG_ID+'/dagRuns/'+c.run_id
    deadline=time.monotonic()+1200
    with (c.out/'airflow-polls.jsonl').open('x') as history:
        while True:
            observed={'run':airflow(path,token),'tasks':airflow(path+'/taskInstances',token)}
            history.write(json.dumps(observed)+'\n');history.flush()
            if observed['run']['state'] in ['success','failed']:break
            if time.monotonic()>=deadline:raise TimeoutError('Inspect the same active native DAG; do not force-retry writers')
            time.sleep(5)
    SHARED.save(c.out/'airflow-final.json',observed);tasks=observed['tasks']['task_instances']
    if observed['run']['state']!='success' or len(tasks)!=6 or any(t['state']!='success' for t in tasks):
        raise ValueError('Actual native RAG tasks failed')
    state=c.deploy/'data/rag'/c.run_id;reports=c.deploy/'outputs'/c.run_id
    native={}
    for name in ['quality.json','datahub-publication.json','retrieval.json','store-readback.json']:
        raw=(state/name).read_bytes();(c.out/name).write_bytes(raw);native[name]=json.loads(raw)
    if (not native['quality.json']['passed'] or not native['datahub-publication.json']['passed']
            or native['datahub-publication.json']['run']!=c.run_id
            or native['datahub-publication.json']['quality_sha256']!=sha256((state/'quality.json').read_bytes()).hexdigest()):
        raise ValueError('Native persisted quality/governance facts do not match this run')
    containers={}
    for operation in ['fetch','chunk','embed','store','validate','publish']:
        raw=(reports/(operation+'-container.json')).read_bytes();value=json.loads(raw)
        if value['State']['Running'] or value['State']['ExitCode']!=0 or value['State']['OOMKilled']:
            raise ValueError('A native RAG task container did not complete successfully')
        expected=deployed['manifest']['metadata_image_id'] if operation=='publish' else deployed['manifest']['image_id']
        if value['Image']!=expected:raise ValueError('The Airflow task executed a different image')
        containers[operation]={'id':value['Id'],'image':value['Image'],'exit_code':value['State']['ExitCode']}
    c.phase('verify',{'dag_id':DAG_ID,'run_id':c.run_id,'actual_successful_tasks':6,'native_containers':containers,
                      'quality_checks':len(native['quality.json']['checks']),
                      'coursework_complete':False,'scope':'Actual Jenkins RAG test/build/auto-deploy, fresh six-task Airflow execution and native Feast/DataHub readback.'})


def run(phase):
    """Execute one declared CI stage, preserving failed stages and never reusing another job's passing receipt."""
    c=Context()
    if phase=='admission':
        proof=SHARED.real_admission(c,5400);c.directory.mkdir(exist_ok=False);c.out.mkdir();c.out.chmod(0o1777)
        SHARED.save(c.out/'admission.json',{'identity':c.identity,'source_digest':c.source_digest,'budget':proof});return
    if phase!='archive':
        with (c.out/(phase+'.attempt')).open('x') as stream:stream.write(datetime.now(timezone.utc).isoformat())
        SHARED.real_admission(c,900)
    if phase=='test':
        c.log('controller-build',['docker','build','-f',str(ROOT/'infra/ci/ServingController.Dockerfile'),
              '--label','org.opencontainers.image.revision='+c.commit,'-t','iot-ci-rag-controller:'+c.release,str(ROOT)])
        controller=json.loads(subprocess.check_output(['docker','image','inspect','iot-ci-rag-controller:'+c.release]))[0]
        SHARED.save(c.out/'controller.json',{'id':controller['Id']})
        c.native('test-host')
    elif phase=='test-host':
        c.log('contracts',[sys.executable,'-B','-m','unittest','tests.test_rag_corpus','tests.test_rag_release','tests.test_llm_ci_contract','-q'])
        c.phase('test',{'source_digest':c.source_digest,'test_console_sha256':sha256((c.out/'contracts.txt').read_bytes()).hexdigest()})
    elif phase=='build':
        c.prior('test');tag='iot-ci-rag:'+c.release
        parent=json.loads(subprocess.check_output(['docker','image','inspect','iot-coursework-rag:deps-c3b696c3955c']))[0]
        if parent['Id']!='sha256:4d65bdfb955f2d459f750fba5f30f381aab0378a4ce95f9047359a5760cd1afd':
            raise ValueError('Use the previously verified native RAG dependency image; do not silently rebuild or upgrade it')
        c.log('application-build',['docker','build','--label','org.opencontainers.image.revision='+c.commit,
              '--build-arg','RUNTIME='+parent['Id'],
              '-f',str(ROOT/'infra/ci/RagApplication.Dockerfile'),'-t',tag,str(ROOT)],1200)
        inspected=json.loads(subprocess.check_output(['docker','image','inspect',tag]))[0];packaged=image_files(inspected['Id'])
        if packaged!={name:c.seal['files'][name] for name in PACKAGED}:raise ValueError('Built RAG application/corpus bytes differ')
        c.phase('build',{'image':tag,'image_id':inspected['Id'],'packaged_sha256':packaged,
                         'dependency_image_id':parent['Id'],
                         'revision_label':inspected['Config']['Labels']['org.opencontainers.image.revision']})
    elif phase=='deploy':
        c.prior('build')
        writable=['data/rag','data/rag-releases','outputs','airflow/dags/ci']
        mounts=[]
        for name in writable:
            mounts+=['--mount','type=bind,src='+str(c.deploy/name)+',dst='+str(c.deploy/name)]
        mounts+=['--mount','type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock']
        mounts+=['--mount','type=bind,src=/usr/bin/docker,dst=/usr/bin/docker,readonly']
        c.native('deploy-host',mounts)
    elif phase=='deploy-host':deploy_host(c)
    elif phase=='verify':
        c.prior('deploy');c.native('verify-host',['--env','IOT_AIRFLOW_USER','--env','IOT_AIRFLOW_PASSWORD'])
    elif phase=='verify-host':verify_host(c)
    elif phase=='archive':
        target=ROOT/'outputs/ci-rag';target.mkdir(parents=True,exist_ok=True)
        if c.out.exists():
            for path in c.out.iterdir():
                if path.is_file():shutil.copyfile(path,target/path.name)
    else:raise ValueError('Unknown RAG CI phase')
    c.source_check()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['admission','test','test-host','build','deploy','deploy-host','verify','verify-host','archive'])
    run(parser.parse_args().phase)
