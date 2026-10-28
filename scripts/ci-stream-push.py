"""Test/build and auto-deploy independent native Feast offline/online stream jobs through private Jenkins and Airflow."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import importlib.util,json,os,re,shutil,subprocess,sys,time
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from feature_store.staging_contract import release,fixture
SPEC=importlib.util.spec_from_file_location('shared_stream_ci',ROOT/'scripts/ci-serving.py');SHARED=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(SHARED)
read=lambda p:json.loads(p.read_bytes())
PACKAGED=['feature_store/'+n+'.py' for n in ['__init__','common','outbox','push','staged_push','staging_contract','staged_checks','staged_job']]+['pipelines/'+n+'.py' for n in ['stream_job','stream_contract','stream_verification']]+['tests/fixtures/stream-finalized-native.json','docs/evidence/stream-push/warm/feast/source-report.json']


class Context(SHARED.Context):
    """Reuse trusted native transports without invoking model selection or altering serving releases."""
    driver='/src/scripts/ci-stream-push.py'
    def __init__(self):
        'Bind the selected store destination to a separate release, output directory and DAG.'
        super().__init__();self.destination=os.environ['IOT_STREAM_DESTINATION'];self.release=release(self.destination,os.environ['BUILD_NUMBER'])
        self.directory=self.deploy/'data/ci/releases'/self.release;self.out=self.directory/'outputs';self.dag='iot_ci_stream_'+self.destination


def save(c,name,value):
    """Write source-bound receipts only in this release."""
    SHARED.save(c.out/name,value)


def api(path,token=None,body=None):
    """Use the existing Jenkins-bound Airflow credentials; never persist auth responses or headers."""
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    request=Request(os.environ['IOT_AIRFLOW_URL']+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
    try:
        with urlopen(request,timeout=30) as r:return json.load(r)
    except HTTPError as e:raise RuntimeError('Private Airflow HTTP '+str(e.code)) from None


def probe(image):
    """Inspect exact packaged modules/input under the non-root runtime without network access."""
    code='import os,pathlib,hashlib,json;assert os.getuid()==10001;p='+repr(PACKAGED)+';print(json.dumps({n:hashlib.sha256((pathlib.Path("/opt/iot-release")/n).read_bytes()).hexdigest() for n in p}))'
    return json.loads(subprocess.check_output(['docker','run','--rm','--network','none','--memory','1g','--cpus','1','--entrypoint','python',image,'-c',code]))


def require_same_runtime(tested,deployed):
    """Compare immutable filesystem layers and execution configuration; image IDs may include different build metadata."""
    keys=['Architecture','Os','RootFS','Config']
    if any(k not in tested or k not in deployed or tested[k]!=deployed[k] for k in keys):raise ValueError('Tested and deployed runtime differ')
    return {'tested_image_id':tested['Id'],'deployed_image_id':deployed['Id'],
            'tested':{k:tested[k] for k in keys},'deployed':{k:deployed[k] for k in keys}}


def verify(c):
    """Require two actual successful native task containers and exact source/stores/replay before Jenkins succeeds."""
    token=api('/auth/token',body={'username':os.environ['IOT_AIRFLOW_USER'],'password':os.environ['IOT_AIRFLOW_PASSWORD']})['access_token']
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        if c.dag in {v['dag_id'] for v in api('/api/v2/dags?limit=100',token)['dags']}:break
        time.sleep(5)
    else:raise RuntimeError('Deployed stream DAG has not imported')
    api('/api/v2/dags/'+c.dag+'/dagRuns',token,{'dag_run_id':c.release,'logical_date':datetime.now(timezone.utc).isoformat(),'conf':{'release':c.release,'commit':c.commit}})
    deadline=time.monotonic()+720;path='/api/v2/dags/'+c.dag+'/dagRuns/'+c.release
    while time.monotonic()<deadline:
        state={'run':api(path,token),'tasks':api(path+'/taskInstances',token)};save(c,'airflow.json',state)
        if state['run']['state'] in ['success','failed']:
            if state['run']['state']!='success' or len(state['tasks']['task_instances'])!=2 or any(v['state']!='success' for v in state['tasks']['task_instances']):raise RuntimeError('Native stream DAG failed; retain task/container evidence')
            break
        time.sleep(5)
    else:raise RuntimeError('The same native stream DAG remains active; inspect it before retrying')
    config=read(c.directory/'release.json');reports={op:read(c.directory/'runs'/(op+'.json')) for op in ['write','replay']}
    for operation,value in reports.items():
        receipt=read(c.directory/'runs'/(operation+'-container.json'))
        if (not value['passed'] or value['destination']!=c.destination or value['source_id']!=config['source_id']
                or value['fixture_parent_sha256']!=fixture(ROOT)['parent_sha256'] or receipt['exit_code']!=0 or receipt['oom_killed']
                or receipt['image_id']!=config['image']['id'] or receipt['commit']!=c.commit):raise ValueError('Native job/source/image receipt differs')
    expected='completed_'+c.destination+'_replay_noop'
    if reports['replay']['modes']!=[expected]*3:raise ValueError('Replay performed another native write')
    if c.destination=='offline':
        if (not all(v['online_all_missing'] and v['offline_rows']==v['historical_rows']==3 and v['machines']==2 for v in reports.values())
                or reports['write']['offline_sha256']!=reports['replay']['offline_sha256']):raise ValueError('Offline history/empty-online/replay differs')
    else:
        if not all(v['acknowledged_windows']==3 and v['machines']==2 and [r['temperature_mean'] for r in v['online_rows']]==[40.0,100.0] for v in reports.values()):raise ValueError('Latest native online vectors differ')
        parent=c.deploy/'data/ci/releases'/config['offline_release'];upstream=read(parent/'runs/replay.json')
        if any(v['offline_sha256_unchanged']!=upstream['offline_sha256'] for v in reports.values()):raise ValueError('Online job modified upstream history')
    save(c,'verification.json',{'passed':True,'commit':c.commit,'release':c.release,'destination':c.destination,'checked_utc':datetime.now(timezone.utc).isoformat(),
        'offline_release':config.get('offline_release'),'reports':reports,'scope':'Jenkins test/build/auto-deploy and actual Airflow native '+c.destination+' job plus replay. Exact retained three-window Kafka/Flink source; no fresh live source run or model rolling-feature claim.'})


def run(phase):
    """Keep each source, release, image and task state distinct; preserve failures and old feature projects."""
    c=Context()
    if phase=='admission':
        proof=SHARED.real_admission(c,5400);c.directory.mkdir(exist_ok=False);c.out.mkdir();c.out.chmod(0o1777);save(c,'admission.json',proof);return
    if phase!='archive':
        with (c.out/(phase+'.attempt')).open('x') as f:f.write(datetime.now(timezone.utc).isoformat())
        if not phase.endswith('-host'):SHARED.real_admission(c)
    if phase=='test':
        fixture(ROOT);c.log('contracts',[sys.executable,'-m','unittest','tests.test_stream_staging_contract','tests.test_stream_runtime_contract','-q'])
        c.log('controller-build',['docker','build','-f',str(ROOT/'infra/ci/ServingController.Dockerfile'),'-t','iot-ci-stream-controller:'+c.release,str(ROOT)])
        save(c,'controller.json',{'id':subprocess.check_output(['docker','image','inspect','iot-ci-stream-controller:'+c.release,'--format','{{.Id}}'],text=True).strip()})
        c.control('discover-host',cluster=True,extra=['--env','IOT_STREAM_DESTINATION'])
        tag='iot-ci-stream-tests:'+c.release;c.log('tests-build',['docker','build','-f',str(ROOT/'infra/ci/StreamPush.Dockerfile'),'-t',tag,str(ROOT)])
        hashes=probe(tag)
        if hashes!={n:c.seal['files'][n] for n in PACKAGED}:raise ValueError('Native test image differs from sealed source')
        folder=c.directory/'tests';folder.mkdir();folder.chmod(0o1777)
        inventory=read(c.out/'inventory.json');command=['docker','run','--name',c.release+'-tests','--network','host','--memory','4g','--memory-swap','4g','--cpus','1',
            '--pids-limit','256','--cap-drop','ALL','--security-opt','no-new-privileges','--read-only','--tmpfs','/tmp:size=128m,mode=1777',
            '--env','IOT_FEAST_REDIS='+inventory['redis_url'],'--mount',f'type=bind,src={folder},dst=/workspace/checks',tag,'-m','feature_store.staged_checks',
            '--operation','sequence','--state','/workspace/checks/phases','--source-id','checks-'+c.release,'--output','/workspace/checks/verification.json']
        c.container('tests',command,memory='4g',cpus='1');report=read(folder/'verification.json')
        if report.get('passed') is not True:raise ValueError('Fresh native store boundary tests failed')
        save(c,'test.json',{'passed':True,'commit':c.commit,'packaged_sha256':hashes,'native_store_phases':list(report['reports'])})
    elif phase=='discover-host':
        service=c.get('get','service','online-serve-jenkins-8','-n','iot-features');address=c.service_url('online-serve-jenkins-8','iot-features').removeprefix('http://')
        save(c,'inventory.json',{'commit':c.commit,'redis_url':address+':6379','redis_service_uid':service['metadata']['uid'],'service':service})
    elif phase=='build':
        if read(c.out/'test.json')['passed'] is not True:raise ValueError('Native tests must pass before building the deployed image')
        base='iot-coursework-feast:0.67.0';base_id=subprocess.check_output(['docker','image','inspect',base,'--format','{{.Id}}'],text=True).strip();tag='iot-ci-stream-push:'+c.release
        c.log('image-build',['docker','build','-f',str(ROOT/'infra/ci/StreamPush.Dockerfile'),'-t',tag,str(ROOT)])
        if probe(tag)!=read(c.out/'test.json')['packaged_sha256'] or subprocess.check_output(['docker','image','inspect',base,'--format','{{.Id}}'],text=True).strip()!=base_id:raise ValueError('Production image/parent changed after tests')
        image={'id':subprocess.check_output(['docker','image','inspect',tag,'--format','{{.Id}}'],text=True).strip(),'tag':tag,'parent_id':base_id,'packaged_sha256':probe(tag)}
        tested=json.loads(subprocess.check_output(['docker','image','inspect',read(c.out/'tests-container.json')['image']]))[0]
        deployed=json.loads(subprocess.check_output(['docker','image','inspect',image['id']]))[0]
        save(c,'runtime-parity.json',require_same_runtime(tested,deployed))
        c.log('image-pip-check',['docker','run','--rm','--network','none','--entrypoint','python',tag,'-m','pip','check'])
        inventory=read(c.out/'inventory.json');parent=os.environ.get('IOT_OFFLINE_RELEASE','');source_id='staged-ci-'+c.release
        config={'release':c.release,'commit':c.commit,'destination':c.destination,'image':image,'redis_url':inventory['redis_url'],'redis_service_uid':inventory['redis_service_uid'],
            'dag_sha256':SHARED.digest(ROOT/'infra/ci/iot_ci_stream_push.py')}
        if c.destination=='online':
            if re.fullmatch(r'stream-offline-jenkins-[1-9][0-9]{0,9}',parent) is None:raise ValueError('Select a completed offline CI release')
            source=c.deploy/'data/ci/releases'/parent;upstream=read(source/'release.json');proof=read(source/'outputs/verification.json')
            if proof['passed'] is not True or upstream['commit']!=c.commit or upstream['destination']!='offline' or upstream['redis_service_uid']!=inventory['redis_service_uid']:raise ValueError('Offline release/source/Redis differs')
            config['offline_release']=parent;source_id=upstream['source_id']
        config['source_id']=source_id
        for name in ['state','runs']:(c.directory/name).mkdir();(c.directory/name).chmod(0o1777)
        SHARED.save(c.directory/'release.json',config);save(c,'build.json',config)
    elif phase=='deploy':
        config=read(c.directory/'release.json');target=c.deploy/'airflow/dags/ci/iot_ci_stream_push.py';target.parent.mkdir(parents=True,exist_ok=True)
        pending=target.with_suffix('.pending');shutil.copyfile(ROOT/'infra/ci/iot_ci_stream_push.py',pending);pending.replace(target)
        if SHARED.digest(target)!=config['dag_sha256']:raise ValueError('Deployed DAG bytes differ')
        save(c,'deployment.json',{'passed':True,'dag':c.dag,'commit':c.commit,'release':c.release,'dag_sha256':SHARED.digest(target),'deployed_utc':datetime.now(timezone.utc).isoformat()})
    elif phase=='verify':verify(c)
    elif phase=='archive':
        target=ROOT/'outputs/ci-stream-push';target.mkdir(parents=True,exist_ok=False)
        if c.out.exists():shutil.copytree(c.out,target/'native')
        for name in ['tests','runs']:
            if (c.directory/name).exists():shutil.copytree(c.directory/name,target/name)
    else:raise ValueError('Unknown stream CI phase')


if __name__=='__main__':run(sys.argv[1])
