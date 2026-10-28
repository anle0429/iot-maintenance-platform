"""Run a sealed Jenkins telemetry release without changing the current production model or its business modules."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import argparse,importlib.util,json,math,os,re,shutil,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.observability_ci import release,environment,same_model,application_paths,patch_predictor,KINDS
from ml.serving_ci import docker_config_digest,check_pods
SPEC=importlib.util.spec_from_file_location('serving_driver',ROOT/'scripts/ci-serving.py')
SHARED=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(SHARED)
read=lambda p:json.loads(p.read_bytes())


class Context(SHARED.Context):
    """Reuse bounded native control transports while isolating this job's paths and driver."""
    driver='/src/scripts/ci-observability.py'
    def __init__(self):
        super().__init__();self.release=release(os.environ['BUILD_NUMBER'])
        self.directory=self.deploy/'data/ci/releases'/self.release;self.out=self.directory/'outputs'


def save(c,name,value):
    """Write source-bound evidence under this build only."""
    SHARED.save(c.out/name,value)


def inventory(c,selection=None):
    """Discover private existing targets and require a genuinely fresh active feature lease."""
    values={kind:json.loads(subprocess.check_output([c.helm,'get','values','iot-'+kind+'-api','-n','iot-features','--all','-o','json'])) for kind in ['feature','drift']}
    env={e['name']:e.get('value') for e in values['feature']['env']}
    match=re.fullmatch(r'http://(iot-pump-serve-jenkins-[1-9][0-9]*)-predictor\.iot-serving\.svc\.cluster\.local',env['IOT_KSERVE_URL'])
    if not match:raise ValueError('Bind this telemetry release to the observed private KServe deployment')
    name=match[1];isvc=c.get('get','inferenceservice',name,'-n','iot-serving')
    urls={kind:c.service_url('iot-'+kind+'-api','iot-features') for kind in ['feature','drift']}
    urls['predictor']=c.service_url(name+'-predictor','iot-serving')
    code,features=SHARED.http(urls['feature'],'/v1/machines/P-001/features')
    if code!=200 or not 0<=(datetime.now(timezone.utc)-datetime.fromisoformat(features['prepared_wall_utc'])).total_seconds()<=2700:
        raise ValueError('Keep at least15minutes of genuine feature lease before telemetry deployment')
    code,metadata=SHARED.http(urls['predictor'],'/v1/models/iot-pump-failure-v1')
    if code!=200:raise ValueError('The active model metadata is unavailable')
    selected=selection or c.selected()
    portable=c.directory/'bundle/portable.json' if selection is not None else c.bundle/'portable.json'
    same_model(selected['pin'],metadata,portable.read_bytes())
    parents={kind:values[kind]['image']['repository']+':'+values[kind]['image']['tag'] for kind in ['feature','drift']}
    parents['predictor']=isvc['spec']['predictor']['containers'][0]['image']
    return {'commit':c.commit,'release':c.release,'values':values,'isvc':isvc,'urls':urls,'parents':parents,
            'model_metadata':metadata,'prepared_wall_utc':features['prepared_wall_utc']}


def probe(c,image,paths):
    """Hash exact packaged files without giving the probe network access or runtime credentials."""
    code='import pathlib,hashlib,json,os;assert os.getuid()==10001;p='+repr(paths)+';print(json.dumps({n:hashlib.sha256(pathlib.Path(v).read_bytes()).hexdigest() for n,v in p.items()}))'
    return json.loads(subprocess.check_output(['docker','run','--rm','--network','none','--read-only','--memory','1g','--cpus','1','--entrypoint','python',image,'-c',code]))


def gate(c):
    """Check the current raw gate artifacts again before any image build or rollout."""
    folder=c.out/'telemetry-tests/gate';report=read(folder/'verification.json')
    spec=importlib.util.spec_from_file_location('telemetry_gate',ROOT/'scripts/test-api-observability.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    score=module.validate_mutation(read(folder/'mutmut-cicd-stats.json'),(folder/'mutmut-all-results.txt').read_text(encoding='utf-8'))
    counts=module.regression_counts(folder/'junit.xml');coverage=read(folder/'coverage.json')
    totals=coverage['totals']
    if (report['commit']!=c.commit or report['passed'] is not True or score!=report['mutation_score_percent']
            or any(report[k]!=v for k,v in counts.items()) or report['mutation_files']!=['services/observed.py']
            or set(coverage['files'])!={'services/observed.py'} or totals['excluded_lines']!=0
            or totals['covered_lines']/totals['num_statements']<=.9 or totals['covered_branches']/totals['num_branches']<=.9):
        raise ValueError('Source, regression, mutation or coverage gate differs')
    return report


def deploy(c):
    """Update the existing three targets only; Helm handles atomic application rollback and the predictor uses guarded patches."""
    old=read(c.out/'inventory.json');build=read(c.out/'build.json');gate(c)
    current=inventory(c)
    if current['parents']!=old['parents'] or current['isvc']['metadata']['uid']!=old['isvc']['metadata']['uid'] or current['values']!=old['values']:
        raise ValueError('Preserve concurrent routing or configuration changes')
    save(c,'deploy-attempt.json',{'commit':c.commit,'release':c.release,'started_utc':datetime.now(timezone.utc).isoformat()})
    name=current['isvc']['metadata']['name'];container=current['isvc']['spec']['predictor']['containers'][0]
    patch=patch_predictor(current['isvc'],build['images']['predictor']['tag'],container.get('env',[]));save(c,'predictor-patch.json',patch)
    c.log('predictor-patch',c.k+['patch','inferenceservice',name,'-n','iot-serving','--type=json','-p',json.dumps(patch)])
    for _ in range(150):
        obj=c.get('get','deployment',name+'-predictor','-n','iot-serving')
        if obj['spec']['template']['spec']['containers'][0]['image']==build['images']['predictor']['tag']:break
        time.sleep(1)
    else:raise RuntimeError('KServe did not reconcile the instrumented predictor')
    c.log('predictor-rollout',c.k+['rollout','status','deployment/'+name+'-predictor','-n','iot-serving','--timeout=180s'])
    for kind in ['feature','drift']:
        value=json.loads(json.dumps(old['values'][kind]));repository,tag=build['images'][kind]['tag'].rsplit(':',1)
        value['image'].update(repository=repository,tag=tag);value['revisionMarker']=c.release;value['env']=environment(value['env'],kind)
        if kind=='feature':value['initContainers'][0]['image']=build['images'][kind]['tag']
        save(c,kind+'-values.json',value)
        c.log(kind+'-helm',[c.helm,'upgrade','iot-'+kind+'-api',str(ROOT/'infra/charts/iot-api'),'-n','iot-features','-f',str(c.out/(kind+'-values.json')),'--atomic','--wait','--timeout','240s'])
    save(c,'deployed.json',{'passed':True,'commit':c.commit,'release':c.release,'model_pin':c.selected()['pin']})


def verify(c):
    """Inspect actual Pod config digests, metric endpoints and all200 predictions with independent portable arithmetic."""
    old=read(c.out/'inventory.json');build=read(c.out/'build.json');pin=c.selected()['pin'];portable=read(c.bundle/'portable.json')
    pod_uids={};names={'feature':('iot-feature-api','iot-features','app.kubernetes.io/instance=iot-feature-api'),
        'drift':('iot-drift-api','iot-features','app.kubernetes.io/instance=iot-drift-api'),
        'predictor':(old['isvc']['metadata']['name']+'-predictor','iot-serving','serving.kserve.io/inferenceservice='+old['isvc']['metadata']['name'])}
    for kind,(name,namespace,selector) in names.items():
        pods=c.get('get','pods','-n',namespace,'-l',selector);image=build['images'][kind]
        pod_uids[kind]=check_pods(pods['items'],image['tag'],image['config_digest'],'kserve-container' if kind=='predictor' else 'api')
        code,ready=SHARED.http(old['urls'][kind],'/health/ready')
        if code!=200:raise ValueError('Instrumented API is not ready')
        save(c,kind+'-pods.json',pods);save(c,kind+'-ready.json',ready)
        (c.out/(kind+'-metrics.prom')).write_text(SHARED.http_text(old['urls'][kind],'/telemetry/metrics'))
    rows=[];maximum=0;operating=stopped=0
    for number in range(1,201):
        machine=f'P-{number:03d}';code,features=SHARED.http(old['urls']['feature'],'/v1/machines/'+machine+'/features')
        if code!=200 or features['machine_id']!=machine or features['prepared_wall_utc']!=old['prepared_wall_utc']:raise ValueError('Feature context changed')
        code,reply=SHARED.http(old['urls']['feature'],'/v1/machines/'+machine+':predict',{})
        rows.append({'machine_id':machine,'features':features,'prediction_status':code,'response':reply})
        if features['operating_context']['status']=='operating':
            same_model(pin,reply.get('model',{}),(c.bundle/'portable.json').read_bytes())
            if code!=200 or reply['feature_trace']!=features:raise ValueError('Prediction response differs')
            vector=features['features'];z=portable['intercept']+sum(((vector[n]-m)/s if s else 0)*w for n,m,s,w in zip(portable['features'],portable['mean'],portable['std'],portable['coefficients'],strict=True))
            score=1/(1+math.exp(-z)) if z>=0 else math.exp(z)/(1+math.exp(z));delta=abs(score-reply['prediction']['risk_score']);maximum=max(maximum,delta)
            if delta>1e-12 or reply['prediction']['predicted_failure']!=int(score>=pin['threshold']):raise ValueError('Portable arithmetic differs')
            operating+=1
        else:
            if code!=409:raise ValueError('Stopped machine must not receive a prediction')
            stopped+=1
    if (operating,stopped)!=(195,5):raise ValueError('The full cohort is incomplete')
    save(c,'http-all-machines.json',rows)
    save(c,'verification.json',{'passed':True,'commit':c.commit,'release':c.release,'checked_utc':datetime.now(timezone.utc).isoformat(),
        'model_pin':pin,'pod_uids':pod_uids,'operating_predictions':operating,'stopped_conflicts':stopped,'max_abs_probability_difference':maximum,
        'scope':'Jenkins test/build/auto-deploy telemetry for the unchanged active production model; not a new model promotion, two-model A-B experiment or fresh Locust result.'})


def run(phase):
    """Run each source-bound phase at most once; preserve previous releases and original failures."""
    c=Context()
    if phase=='admission':
        proof=SHARED.real_admission(c,5400);c.directory.mkdir(exist_ok=False);c.out.mkdir();c.out.chmod(0o1777);save(c,'admission.json',proof);return
    if phase not in ['rollback','archive']:
        with (c.out/(phase+'.attempt')).open('x') as attempt:attempt.write(datetime.now(timezone.utc).isoformat())
        if not phase.endswith('-host'):SHARED.real_admission(c)
    if phase=='select':
        c.log('controller-build',['docker','build','-f',str(ROOT/'infra/ci/ServingController.Dockerfile'),'-t','iot-ci-observability-controller:'+c.release,str(ROOT)])
        image=subprocess.check_output(['docker','image','inspect','iot-ci-observability-controller:'+c.release,'--format','{{.Id}}'],text=True).strip();save(c,'controller.json',{'id':image})
        server=json.loads(subprocess.check_output(['docker','inspect','iot-mlflow-tracking-1']))[0]
        address=server['NetworkSettings']['Networks']['iot-mlflow_default']['IPAddress']
        c.control('select-host',cluster=True,extra=['--add-host','tracking:'+address])
        c.bundle.mkdir(parents=True,exist_ok=False)
        for n in ['portable.json','lineage.json','model-pin.json']:shutil.copyfile(c.directory/'bundle'/n,c.bundle/n)
        c.selected()
    elif phase=='select-host':
        from mlflow.tracking import MlflowClient
        from ml.serving_release import export
        from services.predictor.runtime import PumpPredictor
        bundle=c.directory/'bundle';export(MlflowClient(tracking_uri='http://tracking:5000'),c.version,bundle,ROOT/'ml/protocols/medium-60d-v1.json')
        subprocess.run([sys.executable,str(ROOT/'scripts/write-model-pin.py'),'--bundle',str(bundle),'--output',str(bundle/'model-pin.json')],check=True);bundle.chmod(0o755)
        selection={'commit':c.commit,'release':c.release,'pin':PumpPredictor(bundle).pin,'bundle_sha256':{n:SHARED.digest(bundle/n) for n in ['portable.json','lineage.json','model-pin.json']}}
        save(c,'selection.json',selection);save(c,'inventory.json',inventory(c,selection))
    elif phase=='test':
        c.selected();c.log('contracts',[sys.executable,'-m','unittest','tests.test_observability_ci','-q'])
        base='iot-ci-observability-tests-base:'+c.release;tag='iot-ci-observability-tests:'+c.release
        c.log('tests-base-build',['docker','build','-f',str(ROOT/'infra/ci/ServingTests.Dockerfile'),'-t',base,str(ROOT)])
        base_id=subprocess.check_output(['docker','image','inspect',base,'--format','{{.Id}}'],text=True).strip()
        c.log('tests-overlay-build',['docker','build','--build-arg','BASE_IMAGE='+base_id,'-f',str(ROOT/'infra/observability/Tests.Dockerfile'),'-t',tag,str(ROOT)])
        out=c.out/'telemetry-tests';out.mkdir();out.chmod(0o1777);scratch=Path('/srv/iot-fast/ci')/c.release;scratch.mkdir(exist_ok=False);scratch.chmod(0o1777)
        command=['docker','run','--name',c.release+'-telemetry-tests','--network','none','--memory','4g','--memory-swap','4g','--cpus','2','--pids-limit','256','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--mount',f'type=bind,src={c.host_root},dst=/sealed,readonly','--mount',f'type=bind,src={c.deploy}/data/ci/source/{c.commit}/manifest.json,dst=/manifest.json,readonly',
            '--mount',f'type=bind,src={scratch},dst=/scratch','--mount',f'type=bind,src={out},dst=/results',tag,
            '/sealed/scripts/test-api-observability.py','--source','/sealed','--manifest','/manifest.json','--workspace','/scratch/run','--output','/results/gate']
        c.container('telemetry-tests',command,memory='4g',cpus='2');gate(c)
    elif phase=='build':
        gate(c);selected=c.selected();before=read(c.out/'inventory.json');results={};controller=read(c.out/'controller.json')['id']
        for kind in KINDS:
            parent=before['parents'][kind];base=json.loads(subprocess.check_output(['docker','image','inspect',parent]))[0]
            paths=application_paths(kind,c.seal['files']);expected={n:c.seal['files'][n] for n in paths}
            if kind=='feature':paths['model-pin.json']='/app/model-pin.json';expected['model-pin.json']=selected['bundle_sha256']['model-pin.json']
            if kind=='predictor':paths['portable.json']='/app/model/portable.json';expected['portable.json']=selected['bundle_sha256']['portable.json'];paths['lineage.json']='/app/model/lineage.json'
            actual=probe(c,base['Id'],paths)
            if any(actual[n]!=h for n,h in expected.items()):raise ValueError('Parent business code/model differs from sealed tested source')
            expected=actual;paths['services/observed.py']='/app/services/observed.py';expected['services/observed.py']=c.seal['files']['services/observed.py']
            repository={'feature':'iot-coursework-feature-api','drift':'iot-coursework-drift-api','predictor':'iot-coursework-predictor'}[kind];tag=repository+':'+c.release
            if subprocess.run(['docker','image','inspect',tag],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:raise ValueError('Preserve previous image tag')
            c.log(kind+'-build',['docker','build','--build-arg','BASE_IMAGE='+base['Id'],'--label','org.opencontainers.image.revision='+c.commit,'-f',str(ROOT/'infra/observability/API.Dockerfile'),'-t',tag,str(ROOT)])
            image=json.loads(subprocess.check_output(['docker','image','inspect',tag]))[0]
            if probe(c,image['Id'],paths)!=expected:raise ValueError('Built application differs from tested source/parent weights')
            c.log(kind+'-pip-check',['docker','run','--rm','--network','none','--memory','1g','--cpus','1','--entrypoint','python',image['Id'],'-m','pip','check'])
            exporter=subprocess.Popen(['docker','save',tag],stdout=subprocess.PIPE)
            try:config=docker_config_digest(exporter.stdout)
            finally:exporter.stdout.close();status=exporter.wait()
            if status:raise RuntimeError('Docker image export failed')
            native=['docker','run','--rm','--network','none','--memory','1g','--cpus','1','--mount','type=bind,src=/usr/local/bin/k3s,dst=/usr/local/bin/k3s,readonly',
                '--mount','type=bind,src=/run/k3s/containerd/containerd.sock,dst=/run/k3s/containerd/containerd.sock','--entrypoint','/usr/local/bin/k3s',controller]
            exporter=subprocess.Popen(['docker','save',tag],stdout=subprocess.PIPE)
            try:subprocess.run(native[:2]+['-i']+native[2:]+['ctr','--address','/run/k3s/containerd/containerd.sock','--namespace','k8s.io','images','import','-'],stdin=exporter.stdout,check=True)
            finally:exporter.stdout.close();status=exporter.wait()
            if status:raise RuntimeError('CRI import stream failed')
            cri=json.loads(subprocess.check_output(native+['crictl','--config=/dev/null','--runtime-endpoint=unix:///run/k3s/containerd/containerd.sock','--image-endpoint=unix:///run/k3s/containerd/containerd.sock','inspecti',tag]))
            if cri['status']['id']!=config:raise ValueError('Imported config differs')
            results[kind]={'tag':tag,'image_id':image['Id'],'config_digest':config,'parent_id':base['Id'],'parent_tag':parent,'packaged_sha256':expected}
            save(c,'images-progress.json',results)
        save(c,'build.json',{'commit':c.commit,'release':c.release,'images':results,'model_pin':selected['pin']})
    elif phase in ['deploy','verify']:c.control(phase+'-host',cluster=True,memory='4g')
    elif phase=='deploy-host':deploy(c)
    elif phase=='verify-host':verify(c)
    elif phase=='rollback':
        if (c.out/'deploy-attempt.json').exists():c.control('rollback-host',cluster=True)
    elif phase=='rollback-host':
        old=read(c.out/'inventory.json');build=read(c.out/'build.json');restored=[]
        for kind in ['feature','drift']:
            current=json.loads(subprocess.check_output([c.helm,'get','values','iot-'+kind+'-api','-n','iot-features','--all','-o','json']))
            if current['revisionMarker']==c.release:
                save(c,kind+'-rollback-values.json',old['values'][kind]);c.log(kind+'-rollback',[c.helm,'upgrade','iot-'+kind+'-api',str(ROOT/'infra/charts/iot-api'),'-n','iot-features','-f',str(c.out/(kind+'-rollback-values.json')),'--atomic','--wait','--timeout','240s']);restored.append(kind)
        obj=c.get('get','inferenceservice',old['isvc']['metadata']['name'],'-n','iot-serving');container=obj['spec']['predictor']['containers'][0]
        if obj['metadata']['uid']==old['isvc']['metadata']['uid'] and container['image']==build['images']['predictor']['tag']:
            previous=old['isvc']['spec']['predictor']['containers'][0]
            if container.get('env',[])!=environment(previous.get('env',[]),'predictor'):raise ValueError('Preserve concurrent predictor environment change')
            patch=[{'op':'test','path':'/metadata/resourceVersion','value':obj['metadata']['resourceVersion']},
                {'op':'replace','path':'/spec/predictor/containers/0/image','value':previous['image']},
                {'op':'replace','path':'/spec/predictor/containers/0/env','value':previous.get('env',[])}]
            c.log('predictor-rollback',c.k+['patch','inferenceservice',obj['metadata']['name'],'-n','iot-serving','--type=json','-p',json.dumps(patch)]);restored.append('predictor')
        save(c,'rollback.json',{'restored':restored,'scope':'Conditional restoration commands; this receipt alone does not prove healthy rollback.'})
    elif phase=='archive':
        target=ROOT/'outputs/ci-observability';target.mkdir(parents=True,exist_ok=True)
        if c.out.exists():shutil.copytree(c.out,target,dirs_exist_ok=True)
    else:raise ValueError('Unknown telemetry phase')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('phase');run(parser.parse_args().phase)
