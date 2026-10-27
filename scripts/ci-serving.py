"""Test, package and switch a private feature/KServe production pair from sealed Jenkins source."""
import argparse
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import ipaddress,json,math,os
from pathlib import Path
import shutil,subprocess,sys
from types import SimpleNamespace
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.serving_ci import identity,admission,test_gate,deployment_plan,docker_config_digest,check_prediction,check_pods,check_deployment,host_source_path,check_seed_configmap,ab_gate
from ml.protocol import load_protocol
from services.predictor.binding import validate_pin


def digest(path):
    """Hash exact source/model/evidence bytes without depending on filenames."""
    return sha256(path.read_bytes()).hexdigest()


def save(path,value):
    """Write live CI snapshots atomically; runtime seeds and credentials are never copied to archive here."""
    path.parent.mkdir(parents=True,exist_ok=True);pending=path.with_suffix('.pending')
    pending.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');pending.replace(path)


def http(base,path,body=None):
    """Call only a previously discovered private ClusterIP; retain expected HTTP errors without echoing headers."""
    request=Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'content-type':'application/json'})
    try:
        with urlopen(request,timeout=10) as response:return response.status,json.load(response)
    except HTTPError as error:return error.code,json.load(error)


class Context:
    """Keep every CI phase tied to its sealed source, selected version and release-owned runtime paths."""
    def __init__(self):
        self.commit=os.environ['IOT_CI_COMMIT'];self.version=os.environ['IOT_MODEL_VERSION']
        self.release=identity(os.environ['BUILD_NUMBER'],self.commit,self.version)
        if os.environ.get('IOT_CI_PROVIDER')!='jenkins' or not os.environ.get('JENKINS_URL'):raise ValueError('Use the existing trusted private Jenkins job')
        self.deploy=Path(os.environ['IOT_DEPLOY_ROOT']).resolve();self.directory=self.deploy/'data/ci/releases'/self.release
        self.host_root=host_source_path(ROOT,self.deploy)
        self.out=self.directory/'outputs';self.bundle=ROOT/'models/release'
        self.seal=json.loads((self.deploy/'data/ci/source'/self.commit/'manifest.json').read_text())
        if self.seal.get('format')!='iot-sealed-jenkins-serving-v1' or self.seal['commit']!=self.commit:raise ValueError('Serving source seal differs')
        self.source_check()
        self.k=['/usr/local/bin/k3s','kubectl'];self.helm=str(self.deploy/'data/runtime/kubernetes-assets-27/helm/helm')

    def source_check(self):
        """Refuse traversal, missing files or edits between test/build/deployment phases."""
        for name,expected in self.seal['files'].items():
            path=(ROOT/name).resolve()
            if not path.is_relative_to(ROOT) or not path.is_file() or digest(path)!=expected:raise ValueError('Serving source changed: '+name)

    def log(self,name,command,timeout=600):
        """Save the native command console and propagate its failure to Jenkins."""
        with (self.out/(name+'.txt')).open('w') as stream:subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=timeout)

    def get(self,*args):
        """Read Kubernetes JSON using the already mounted private kubeconfig."""
        return json.loads(subprocess.check_output(self.k+list(args)+['-o','json'],text=True))

    def service_url(self,name,namespace):
        """Require internal IPv4 ClusterIP services; never publish a load balancer or public port."""
        value=self.get('get','service',name,'-n',namespace);spec=value['spec'];address=ipaddress.ip_address(spec['clusterIP'])
        if spec.get('type','ClusterIP')!='ClusterIP' or spec.get('externalIPs') or not address.is_private or address.version!=4:
            raise ValueError('Serving CI requires a private IPv4 ClusterIP')
        return 'http://'+str(address)

    def selected(self):
        """Check the exported bundle and baked pin again before trusting an earlier phase's receipt."""
        value=json.loads((self.out/'selection.json').read_text())
        if value['commit']!=self.commit or value['release']!=self.release or value['pin']['version']!=self.version:raise ValueError('Selection belongs to another CI build')
        for name,h in value['bundle_sha256'].items():
            if digest(self.bundle/name)!=h or digest(self.directory/'bundle'/name)!=h:raise ValueError('Selected model asset changed')
        pin=validate_pin(json.loads((self.bundle/'model-pin.json').read_text()))
        if pin!=value['pin'] or pin['production'] is not True:raise ValueError('Selected model is not this production release')
        return value

    def container(self,name,command,memory='2g',cpus='1'):
        """Keep exact native limits/exit/OOM receipts and remove only this release's completed container."""
        try:self.log(name,command,3000)
        finally:
            inspected=subprocess.run(['docker','inspect',self.release+'-'+name],capture_output=True,text=True)
            if inspected.returncode==0:
                value=json.loads(inspected.stdout)[0];state=value['State']
                save(self.out/(name+'-container.json'),{'id':value['Id'],'image':value['Image'],
                    'state':{k:state.get(k) for k in ['ExitCode','OOMKilled','StartedAt','FinishedAt','Running']},
                    'memory_bytes':value['HostConfig']['Memory'],'nano_cpus':value['HostConfig']['NanoCpus'],
                    'requested_memory':memory,'requested_cpus':cpus,'commit':self.commit})
                if not state['Running']:subprocess.run(['docker','rm',value['Id']],check=True,stdout=subprocess.DEVNULL)
                if state.get('OOMKilled') or state.get('Running') or state.get('ExitCode')!=0:raise RuntimeError('Serving CI container did not complete successfully')

    def control(self,phase,cluster=False,memory='2g',extra=None):
        """Mount source read-only and only this release writable; kubeconfig exists solely in private control calls."""
        image=json.loads((self.out/'controller.json').read_text())['id'];name=phase.removesuffix('-host')
        command=['docker','run','--name',self.release+'-'+name,'--memory',memory,'--memory-swap',memory,'--cpus','1',
            '--network','host','--entrypoint','python','--env','PYTHONPATH=/src',
            '--mount',f'type=bind,src={self.host_root},dst=/src,readonly','--mount',f'type=bind,src={self.deploy},dst={self.deploy},readonly',
            '--mount',f'type=bind,src={self.directory},dst={self.directory}']
        for var in ['IOT_CI_COMMIT','IOT_MODEL_VERSION','BUILD_NUMBER','IOT_CI_PROVIDER','JENKINS_URL','IOT_DEPLOY_ROOT']:command+=['--env',var]
        if cluster:
            command+=['--mount','type=bind,src=/usr/local/bin/k3s,dst=/usr/local/bin/k3s,readonly',
                '--mount','type=bind,src=/etc/rancher/k3s/k3s.yaml,dst=/etc/rancher/k3s/k3s.yaml,readonly',
                '--mount',f'type=bind,src={self.helm},dst={self.helm},readonly','--env','KUBECONFIG=/etc/rancher/k3s/k3s.yaml']
        command+=(extra or [])+[image,'/src/scripts/ci-serving.py',phase]
        self.container(name,command,memory)


def real_admission(c,seconds=900):
    """Observe real mounted disks, RAM and the authorized session deadline before native work."""
    if (not (Path('/srv/iot-data').is_mount() or c.deploy.is_mount())
            or not (Path('/srv/iot-fast').is_mount() or Path('/srv/iot-fast/ci').is_mount()) or not c.deploy.is_relative_to('/srv/iot-data')):
        raise ValueError('Use the existing mounted coursework data disks')
    session=json.loads((c.deploy/'data/runtime/ci/session.json').read_text())
    ram=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024
    return admission(session['auto_stop_utc'],datetime.now(timezone.utc),ram,[shutil.disk_usage(c.deploy).free,shutil.disk_usage('/srv/iot-fast/ci').free],seconds)


def plan(c):
    """Derive paired manifests from the actual checked-in Kubernetes/Helm templates."""
    import yaml
    selected=c.selected();isvc=next(v for v in yaml.safe_load_all((ROOT/'infra/kserve/pump-failure.yaml').read_text()) if v['kind']=='InferenceService')
    redis=[v for v in yaml.safe_load_all((ROOT/'infra/feature-api/redis.yaml').read_text()) if v['kind']!='Namespace']
    values=yaml.safe_load((ROOT/'infra/feature-api/helm-values.yaml').read_text())
    return deployment_plan(os.environ['BUILD_NUMBER'],c.commit,selected['pin'],isvc,redis,values)


def scoped_receipt(c,name):
    """Reject a receipt copied from a different source/build rather than accepting its passed flag."""
    value=json.loads((c.out/name).read_text())
    if value.get('commit')!=c.commit or value.get('release')!=c.release:raise ValueError('Serving receipt belongs to another source/build')
    return value


def staged_plan(c):
    """Seal generated routing/seed inputs and truthful lease before switching or verifying this release."""
    selected=c.selected();p=scoped_receipt(c,'plan.json');stage=scoped_receipt(c,'staged.json')
    if (stage.get('passed') is not True or p['pin']!=selected['pin'] or stage['model_pin']!=selected['pin']
            or stage['plan_sha256']!=digest(c.out/'plan.json') or stage['values_sha256']!=digest(c.out/'feature-values.json')
            or json.loads((c.out/'feature-values.json').read_text())!=p['feature_values']):raise ValueError('Staged routing/configuration changed')
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(stage['prepared_wall_utc'])).total_seconds()
    if not 0<=age<3300:raise ValueError('Native seed lease is unavailable or too old to switch/verify')
    check_seed_configmap(c.get('get','configmap',p['seed_configmap'],'-n','iot-features'),stage['seed_sha256'],stage['seed_uid'])
    return p,stage


def stage_host(c):
    """Create an isolated model/store, materialize truthful fresh state and verify the new predictor before switching traffic."""
    from feature_store.serving import run as prepare
    from services.feature_api.runtime import FeastKServeBackend,FeatureRuntime
    from services.predictor.runtime import PumpPredictor
    from services.predictor.contracts import FeatureVector
    p=plan(c);build=scoped_receipt(c,'build.json');save(c.out/'plan.json',p)
    if build['pin']!=p['pin']:raise ValueError('Built model differs from the staged plan')
    c.get('get','serviceaccount','iot-predictor','-n','iot-serving')
    for kind,name,namespace in [('inferenceservice',p['isvc']['metadata']['name'],'iot-serving'),
            ('deployment',p['redis'][0]['metadata']['name'],'iot-features'),('service',p['redis'][1]['metadata']['name'],'iot-features')]:
        found=subprocess.check_output(c.k+['get',kind,name,'-n',namespace,'--ignore-not-found','-o','name'],text=True).strip()
        if found:raise ValueError('Preserve an existing release resource: '+name)
    spec=c.out/'stage-resources.json';save(spec,{'apiVersion':'v1','kind':'List','items':p['redis']+[p['isvc']]})
    c.log('stage-create',c.k+['create','-f',str(spec)])
    c.log('redis-ready',c.k+['rollout','status','deployment/'+p['redis'][0]['metadata']['name'],'-n','iot-features','--timeout=120s'])
    c.log('model-ready',c.k+['wait','--for=condition=Ready','inferenceservice/'+p['isvc']['metadata']['name'],'-n','iot-serving','--timeout=300s'])
    endpoint=c.service_url(p['isvc']['metadata']['name']+'-predictor','iot-serving')
    model=PumpPredictor(c.bundle);samples=json.loads((ROOT/'tests/fixtures/predictor-native-spark.json').read_text())['samples']
    code,reply=http(endpoint,'/v1/models/iot-pump-failure-v1:predict',{'instances':[s['features'] for s in samples]})
    expected=model.predict([FeatureVector.model_validate(s['features']) for s in samples])
    if code!=200 or len(reply['predictions'])!=len(expected):raise ValueError('Staged KServe did not score all probes')
    for actual,reference in zip(reply['predictions'],expected,strict=True):
        check_prediction(actual,reply['model'],p['pin'])
        if abs(actual['risk_score']-reference['risk_score'])>1e-12:raise ValueError('Staged predictor differs from the selected portable model')
    pods=c.get('get','pods','-n','iot-serving','-l','serving.kserve.io/inferenceservice='+p['isvc']['metadata']['name'])
    check_pods(pods['items'],p['predictor_image'],build['images']['predictor']['config_digest'],'kserve-container')
    redis_url=c.service_url(p['redis'][1]['metadata']['name'],'iot-features');os.environ['IOT_FEAST_REDIS']=redis_url.removeprefix('http://')+':6379'
    protocol=load_protocol(ROOT/'ml/protocols/medium-60d-v1.json');cutoff=datetime.fromisoformat(protocol['chronological_split']['observation_end_exclusive'].replace('Z','+00:00'))-timedelta(minutes=10)
    state=c.directory/'runtime/state'
    prepare(SimpleNamespace(state=state,source_state=c.deploy/'data/feast/batch-medium-27',batch_report=c.deploy/'outputs/feast-batch-27/batch/report.json',
        protocol=ROOT/'ml/protocols/medium-60d-v1.json',silver=c.deploy/'data/lakehouse/large-ingest-medium-27-v4',
        input_binding=c.deploy/'outputs/feature-medium-27-v2/input-binding.json',cutoff=cutoff.isoformat()))
    backend=FeastKServeBackend(state,endpoint);runtime=FeatureRuntime(backend.context,backend,model_pin=p['pin'])
    for machine,status in backend.context['machines'].items():
        runtime.features(machine)
        if status['status']=='operating':runtime.predict(machine)
    seed=c.k+['create','configmap',p['seed_configmap'],'-n','iot-features','--dry-run=client','-o','json']
    files={'context.json':state/'context.json','verification.json':state/'verification.json','_READY.json':state/'_READY.json','registry.db':state/'registry/registry.db'}
    if sum(f.stat().st_size for f in files.values())>700_000:raise ValueError('Seed would exceed the bounded ConfigMap budget')
    for name,path in files.items():seed+=['--from-file='+name+'='+str(path)]
    payload=json.loads(subprocess.check_output(seed,text=True));payload['immutable']=True;payload['metadata'].setdefault('labels',{})['iot.coursework/release']=c.release
    subprocess.run(c.k+['create','-f','-'],input=json.dumps(payload),text=True,check=True,stdout=subprocess.DEVNULL)
    save(c.out/'feature-values.json',p['feature_values'])
    obj=c.get('get','inferenceservice',p['isvc']['metadata']['name'],'-n','iot-serving')
    hashes={name:digest(path) for name,path in files.items()}
    seed_uid=check_seed_configmap(c.get('get','configmap',p['seed_configmap'],'-n','iot-features'),hashes)
    save(c.out/'staged.json',{'passed':True,'commit':c.commit,'release':c.release,'isvc_uid':obj['metadata']['uid'],
        'model_pin':p['pin'],'prepared_wall_utc':backend.context['prepared_wall_utc'],
        'plan_sha256':digest(c.out/'plan.json'),'values_sha256':digest(c.out/'feature-values.json'),
        'seed_sha256':hashes,'seed_uid':seed_uid,'machines':len(backend.context['machines']),
        'staged_utc':datetime.now(timezone.utc).isoformat(),'scope':'New private model/Redis/seed verified; active feature release not switched yet.'})


def verify_host(c):
    """Check real pods and all HTTP vectors against a separate offline query and portable arithmetic."""
    import duckdb
    from services.predictor.runtime import PumpPredictor
    p,stage=staged_plan(c);build=scoped_receipt(c,'build.json')
    model=PumpPredictor(c.bundle);context=json.loads((c.directory/'runtime/state/context.json').read_text())
    c.log('feature-rollout',c.k+['rollout','status','deployment/iot-feature-api','-n','iot-features','--timeout=180s'])
    service=c.service_url('iot-feature-api','iot-features');deployment=c.get('get','deployment','iot-feature-api','-n','iot-features');check_deployment(deployment,p)
    feature_pods=c.get('get','pods','-n','iot-features','-l','app.kubernetes.io/instance=iot-feature-api')
    model_pods=c.get('get','pods','-n','iot-serving','-l','serving.kserve.io/inferenceservice='+p['isvc']['metadata']['name'])
    check_pods(feature_pods['items'],p['feature_image'],build['images']['feature']['config_digest'],'api')
    check_pods(model_pods['items'],p['predictor_image'],build['images']['predictor']['config_digest'],'kserve-container')
    isvc=c.get('get','inferenceservice',p['isvc']['metadata']['name'],'-n','iot-serving')
    if isvc['metadata']['uid']!=stage['isvc_uid'] or not any(x['type']=='Ready' and x['status']=='True' for x in isvc['status']['conditions']):raise ValueError('Staged KServe identity/readiness changed')
    compact=c.deploy/'data/feast/batch-medium-27/machine_rolling.parquet';fields=model.model['features']
    with duckdb.connect() as connection:
        connection.execute("SET threads=1");connection.execute("SET memory_limit='2GB'")
        values=connection.execute('SELECT machine_id,'+','.join(fields)+' FROM read_parquet(?) WHERE event_timestamp<=? QUALIFY row_number() OVER(PARTITION BY machine_id ORDER BY event_timestamp DESC)=1',
            [str(compact),datetime.fromisoformat(context['as_of']).replace(tzinfo=None)]).fetchall()
    offline={row[0]:dict(zip(fields,row[1:],strict=True)) for row in values}
    if set(offline)!=set(context['machines']):raise ValueError('Offline HTTP cohort differs')
    probes=[];deltas=[];stopped=0
    for machine,status in context['machines'].items():
        code,features=http(service,'/v1/machines/'+machine+'/features')
        if code!=200 or set(features['features'])!=set(fields) or not all(math.isclose(features['features'][n],offline[machine][n],rel_tol=1e-12,abs_tol=1e-12) for n in fields):raise ValueError('HTTP features differ from immutable offline source')
        code,reply=http(service,'/v1/machines/'+machine+':predict',{})
        if status['status']=='operating':
            if code!=200:raise ValueError('Operating machine prediction failed')
            check_prediction(reply['prediction'],reply['model'],p['pin'])
            if reply['feature_trace']!=features or features['protocol_sha256']!=p['pin']['protocol_sha256'] or features['as_of']!=context['as_of']:raise ValueError('Feature trace differs')
            z=model.model['intercept']+sum(((offline[machine][n]-mean)/std if std else 0)*coef for n,mean,std,coef in zip(fields,model.model['mean'],model.model['std'],model.model['coefficients'],strict=True))
            expected=1/(1+math.exp(-z)) if z>=0 else math.exp(z)/(1+math.exp(z));delta=abs(expected-reply['prediction']['risk_score']);deltas.append(delta)
            if delta>1e-12 or reply['prediction']['predicted_failure']!=int(expected>=p['pin']['threshold']):raise ValueError('HTTP prediction differs from independent arithmetic')
        elif code!=409:raise ValueError('Stopped machine must not be predicted')
        else:stopped+=1
        probes.append({'machine_id':machine,'features':features,'prediction_status':code,'response':reply})
    if not deltas:raise ValueError('No operating predictions verified')
    for name,value in [('http-all-machines.json',probes),('feature-deployment.json',deployment),('feature-pods.json',feature_pods),('model-pods.json',model_pods),('inferenceservice.json',isvc)]:save(c.out/name,value)
    save(c.out/'verification.json',{'passed':True,'commit':c.commit,'release':c.release,'model_pin':p['pin'],'isvc_uid':isvc['metadata']['uid'],
        'machines':len(probes),'operating_predictions':len(deltas),'stopped_rejected':stopped,'max_abs_score_delta':max(deltas),
        'compact_sha256':digest(compact),'checked_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'Native paired serving CI, real pod configs and all-machine HTTP/offline equality. No new training, quality evaluation or Locust claim.'})


def run(phase):
    """Execute one native phase; failure preserves source, old endpoints and the release's original receipts."""
    c=Context()
    if phase=='admission':
        proof=real_admission(c,3600);c.directory.mkdir(parents=True,exist_ok=False);c.out.mkdir();c.out.chmod(0o1777);save(c.out/'admission.json',proof);return
    if phase not in ['cleanup','archive']:
        with (c.out/(phase+'.attempt')).open('x') as stream:stream.write(datetime.now(timezone.utc).isoformat())
        if not phase.endswith('-host'):save(c.out/(phase+'-admission.json'),real_admission(c))
    if phase=='select':
        c.log('controller-build',['docker','build','-f',str(ROOT/'infra/ci/ServingController.Dockerfile'),'-t','iot-ci-serving-controller:'+c.release,str(ROOT)])
        image=subprocess.check_output(['docker','image','inspect','iot-ci-serving-controller:'+c.release,'--format','{{.Id}}'],text=True).strip();save(c.out/'controller.json',{'id':image})
        ids=subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.project=iot-mlflow','--filter','label=com.docker.compose.service=tracking'],text=True).split()
        if len(ids)!=1:raise ValueError('Find exactly one existing private registry')
        net=json.loads(subprocess.check_output(['docker','inspect',ids[0]],text=True))[0]['NetworkSettings']['Networks'];address=net['iot-mlflow_default']['IPAddress']
        if not ipaddress.ip_address(address).is_private:raise ValueError('Registry must remain private')
        c.control('select-host',extra=['--add-host','tracking:'+address])
        c.bundle.mkdir(parents=True,exist_ok=False)
        for name in ['portable.json','lineage.json','model-pin.json']:shutil.copyfile(c.directory/'bundle'/name,c.bundle/name)
        c.selected()
    elif phase=='select-host':
        from mlflow.tracking import MlflowClient
        from ml.serving_release import export
        from services.predictor.runtime import PumpPredictor
        bundle=c.directory/'bundle';export(MlflowClient(tracking_uri='http://tracking:5000'),c.version,bundle,ROOT/'ml/protocols/medium-60d-v1.json')
        subprocess.run([sys.executable,str(ROOT/'scripts/write-model-pin.py'),'--bundle',str(bundle),'--output',str(bundle/'model-pin.json')],check=True)
        save(c.out/'selection.json',{'commit':c.commit,'release':c.release,'pin':PumpPredictor(bundle).pin,
            'bundle_sha256':{n:digest(bundle/n) for n in ['portable.json','lineage.json','model-pin.json']},'exported_utc':datetime.now(timezone.utc).isoformat()})
    elif phase=='test':
        c.selected();tag='iot-ci-serving-tests:'+c.release
        c.log('tests-build',['docker','build','-f',str(ROOT/'infra/ci/ServingTests.Dockerfile'),'-t',tag,str(ROOT)])
        output=c.out/'tests';output.mkdir();output.chmod(0o1777)
        c.container('tests',['docker','run','--name',c.release+'-tests','--network','none','--memory','4g','--memory-swap','4g','--cpus','2',
            '--pids-limit','256','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--mount',f'type=bind,src={output},dst=/results',tag,'--output','/results'],memory='4g',cpus='2')
    elif phase=='build':
        selected=c.selected();tests=c.out/'tests'
        test_gate(json.loads((tests/'coverage.json').read_text()),json.loads((tests/'mutmut-cicd-stats.json').read_text()),(tests/'mutmut-all-results.txt').read_text())
        images={};controller=json.loads((c.out/'controller.json').read_text())['id']
        for kind in ['predictor','feature']:
            tag='iot-coursework-'+('predictor' if kind=='predictor' else 'feature-api')+':'+c.release
            directory='predictor' if kind=='predictor' else 'feature-api';argument='MODEL_BUNDLE=models/release' if kind=='predictor' else 'MODEL_PIN=models/release/model-pin.json'
            c.log(kind+'-build',['docker','build','--build-arg',argument,'--label','org.opencontainers.image.revision='+c.commit,'-f',str(ROOT/'infra'/directory/'Dockerfile'),'-t',tag,str(ROOT)])
            image=json.loads(subprocess.check_output(['docker','image','inspect',tag],text=True))[0]
            names=['services/__init__.py',*('services/'+directory.replace('-','_')+'/'+p.name for p in (ROOT/'services'/directory.replace('-','_')).glob('*.py')),'ml/__init__.py','ml/protocol.py']
            if kind=='predictor':names+=['ml/training_contract.py']
            else:names+=['services/predictor/'+n+'.py' for n in ['__init__','contracts','binding']]
            paths={n:'/app/'+n for n in names}
            if kind=='predictor':paths.update({'models/release/portable.json':'/app/model/portable.json','models/release/lineage.json':'/app/model/lineage.json'})
            else:paths['models/release/model-pin.json']='/app/model-pin.json'
            expected={n:digest(ROOT/n) for n in paths}
            code='import json,pathlib,hashlib; p='+repr(paths)+"; print(json.dumps({n:hashlib.sha256(pathlib.Path(v).read_bytes()).hexdigest() for n,v in p.items()}))"
            actual=json.loads(subprocess.check_output(['docker','run','--rm','--network','none','--memory','256m','--cpus','.5','--entrypoint','python',image['Id'],'-c',code],text=True))
            if actual!=expected or image['Config']['Labels']['org.opencontainers.image.revision']!=c.commit:raise ValueError('Packaged application differs from tested source/model')
            exporter=subprocess.Popen(['docker','save',image['Id']],stdout=subprocess.PIPE)
            try:config=docker_config_digest(exporter.stdout)
            finally:
                exporter.stdout.close()
                if exporter.wait()!=0:raise RuntimeError('Image config export failed')
            base=['docker','run','--rm','--network','none','--memory','1g','--cpus','1',
                '--mount','type=bind,src=/usr/local/bin/k3s,dst=/usr/local/bin/k3s,readonly',
                '--mount','type=bind,src=/run/k3s/containerd/containerd.sock,dst=/run/k3s/containerd/containerd.sock','--entrypoint','/usr/local/bin/k3s',controller]
            exporter=subprocess.Popen(['docker','save',tag],stdout=subprocess.PIPE)
            try:subprocess.run(base[:2]+['-i']+base[2:]+['ctr','--address','/run/k3s/containerd/containerd.sock','--namespace','k8s.io','images','import','-'],stdin=exporter.stdout,check=True)
            finally:
                exporter.stdout.close()
                if exporter.wait()!=0:raise RuntimeError('Image import export failed')
            cri=json.loads(subprocess.check_output(base+['crictl','--config=/dev/null','--runtime-endpoint=unix:///run/k3s/containerd/containerd.sock',
                '--image-endpoint=unix:///run/k3s/containerd/containerd.sock','inspecti',tag],text=True))
            current=subprocess.check_output(['docker','image','inspect',tag,'--format','{{.Id}}'],text=True).strip()
            if cri['status']['id']!=config or current!=image['Id']:raise ValueError('Imported CRI config/tag differs from the verified Docker image')
            images[kind]={'tag':tag,'id':image['Id'],'config_digest':config,'packaged_sha256':expected}
        save(c.out/'build.json',{'commit':c.commit,'release':c.release,'pin':selected['pin'],'images':images})
    elif phase=='stage':c.selected();c.control('stage-host',cluster=True,memory='8g')
    elif phase=='stage-host':stage_host(c)
    elif phase=='switch':c.selected();c.control('switch-host',cluster=True)
    elif phase=='switch-host':
        p,stage=staged_plan(c)
        evidence=c.out/'ab-verification.json'
        if not evidence.exists():raise ValueError('Full cutover is blocked until native A/B routing and monitoring are implemented and verified for this release')
        ab_gate(scoped_receipt(c,'ab-verification.json'),p['pin'],stage['isvc_uid'])
        status=json.loads(subprocess.check_output([c.helm,'status','iot-feature-api','-n','iot-features','-o','json'],text=True))
        values=json.loads(subprocess.check_output([c.helm,'get','values','iot-feature-api','-n','iot-features','--all','-o','json'],text=True))
        save(c.out/'previous.json',{'revision':status['version'],'marker':values.get('revisionMarker'),'observed_utc':datetime.now(timezone.utc).isoformat()})
        c.log('chart-lint',[c.helm,'lint',str(ROOT/'infra/charts/iot-api'),'-f',str(c.out/'feature-values.json')])
        c.log('helm-upgrade',[c.helm,'upgrade','--install','iot-feature-api',str(ROOT/'infra/charts/iot-api'),'-n','iot-features',
            '-f',str(c.out/'feature-values.json'),'--atomic','--wait','--timeout','240s'],600)
        save(c.out/'switched.json',{'commit':c.commit,'release':c.release,'completed_utc':datetime.now(timezone.utc).isoformat()})
    elif phase=='verify':c.selected();c.control('verify-host',cluster=True,memory='4g')
    elif phase=='verify-host':verify_host(c)
    elif phase=='cleanup':
        for suffix in ['tests','select','stage','switch','verify']:
            ids=subprocess.check_output(['docker','ps','-q','--filter','name=^/'+c.release+'-'+suffix+'$'],text=True).split()
            if ids:subprocess.run(['docker','stop','--time','20',*ids],check=True,stdout=subprocess.DEVNULL)
        if (c.out/'previous.json').exists() and (c.out/'controller.json').exists():c.control('rollback-host',cluster=True)
    elif phase=='rollback-host':
        previous=json.loads((c.out/'previous.json').read_text());values=json.loads(subprocess.check_output([c.helm,'get','values','iot-feature-api','-n','iot-features','--all','-o','json'],text=True))
        attempted=values.get('revisionMarker')==c.release
        if attempted:c.log('explicit-rollback',[c.helm,'rollback','iot-feature-api',str(previous['revision']),'-n','iot-features','--wait','--timeout','240s'])
        save(c.out/'rollback.json',{'requested':attempted,'previous_revision':previous['revision'],'current_marker_before_cleanup':values.get('revisionMarker'),
            'scope':'Restore only this build switch; old endpoints/seeds retained. This command alone is not proof of healthy fallback.'})
    elif phase=='archive':
        target=ROOT/'outputs/ci-serving';target.mkdir(parents=True,exist_ok=True)
        if c.out.exists():shutil.copytree(c.out,target,dirs_exist_ok=True)
    else:raise ValueError('Unknown serving CI phase')
    c.source_check()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['admission','select','select-host','test','build','stage','stage-host','switch','switch-host','verify','verify-host','cleanup','rollback-host','archive'])
    run(parser.parse_args().phase)
