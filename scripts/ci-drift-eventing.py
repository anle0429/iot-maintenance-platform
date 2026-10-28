"""Pull production lineage, test/build and auto-deploy a private Eventing-to-KServe drift receiver in Jenkins."""
from datetime import datetime,timezone
from pathlib import Path
from hashlib import sha256
import importlib.util,json,os,sqlite3,subprocess,sys,time,urllib.request,urllib.error,urllib.parse
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.drift_eventing import plan,event_headers,private_services,NAMESPACE
from ml.observability_ci import registry_address,local_base
from ml.serving_ci import docker_config_digest,check_pods
SPEC=importlib.util.spec_from_file_location('shared',ROOT/'scripts/ci-serving.py');SHARED=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(SHARED)
GATE_SPEC=importlib.util.spec_from_file_location('event_gate',ROOT/'scripts/test-drift-eventing.py');GATE=importlib.util.module_from_spec(GATE_SPEC);GATE_SPEC.loader.exec_module(GATE)
read=lambda p:json.loads(p.read_bytes())


class Context(SHARED.Context):
    """Use independent releases while reusing bounded native controller transports."""
    driver='/src/scripts/ci-drift-eventing.py'
    def __init__(self):
        'Bind this Jenkins build to its isolated eventing release and output directory.'
        super().__init__();self.release='drift-event-jenkins-'+os.environ['BUILD_NUMBER']
        self.directory=self.deploy/'data/ci/releases'/self.release;self.out=self.directory/'outputs'


def save(c,name,value):
    """Keep every receipt inside the current source-bound release."""
    SHARED.save(c.out/name,value)


def gate(c):
    """Recheck actual raw counts and full mutation inventory before building or deploying."""
    folder=c.out/'tests/gate';report=read(folder/'verification.json')
    score=GATE.validate(read(folder/'coverage.json'),read(folder/'mutmut-cicd-stats.json'),(folder/'mutmut-all-results.txt').read_text())
    if report.get('passed') is not True or report['commit']!=c.commit or score!=report['mutation_score_percent']:raise ValueError('Gate source/score differs')
    return report


def probe(image,paths):
    """Read hashes and non-root identity without network or credentials."""
    code='import os,pathlib,hashlib,json;assert os.getuid()==10001;p='+repr(paths)+';print(json.dumps({k:hashlib.sha256(pathlib.Path(v).read_bytes()).hexdigest() for k,v in p.items()}))'
    return json.loads(subprocess.check_output(['docker','run','--rm','--network','none','--read-only','--memory','1g','--cpus','1','--entrypoint','python',image,'-c',code]))


def deploy(c):
    """Create a separate release and ledger without replacing the current synchronous drift API or prior pilot."""
    gate(c);selected=c.selected();inventory=read(c.out/'inventory.json');build=read(c.out/'build.json')
    namespace=c.get('get','namespace',NAMESPACE)
    if namespace['metadata']['uid']!=inventory['namespace_uid']:raise ValueError('Namespace changed')
    ledger=c.directory/'runtime/eventing'/c.release;ledger.mkdir(parents=True,exist_ok=False);os.chown(ledger,10001,10001);ledger.chmod(0o750)
    p=plan(c.release,build['tag'],inventory['node'],(c.out/'reference.json').read_text(),selected['pin'],read(c.bundle/'portable.json'),str(ledger));save(c,'plan.json',p)
    objects=[o for o in p['objects'] if o['kind']!='Namespace']
    for obj in objects:
        args=['get',obj['kind'],obj['metadata']['name']]
        if 'namespace' in obj['metadata']:args+=['-n',NAMESPACE]
        if subprocess.check_output(c.k+args+['--ignore-not-found','-o','name'],text=True).strip():raise ValueError('Preserve an existing release resource')
    save(c,'resources.json',{'apiVersion':'v1','kind':'List','items':objects})
    c.log('create',c.k+['create','-f',str(c.out/'resources.json')])
    c.log('kserve-ready',c.k+['wait','--for=condition=Ready','inferenceservice/'+p['name'],'-n',NAMESPACE,'--timeout=240s'],250)
    c.log('eventing-ready',c.k+['wait','--for=condition=Ready','broker/'+p['broker'],'trigger/'+p['name']+'-trigger','-n',NAMESPACE,'--timeout=240s'],250)


def verify(c):
    """Compare actual subscriber counters, persistent rows and direct readback; Broker ACK alone is insufficient."""
    p=read(c.out/'plan.json');build=read(c.out/'build.json');c.selected();gate(c)
    for kind in ['inferenceservices','brokers','triggers','pods','services','persistentvolumeclaims']:save(c,kind+'.json',c.get('get',kind,'-n',NAMESPACE))
    services=c.get('get','services','-n',NAMESPACE)['items'];save(c,'resolved-services.json',private_services(services,lambda n,ns:c.get('get','service',n,'-n',ns)))
    pods=c.get('get','pods','-n',NAMESPACE,'-l','serving.kserve.io/inferenceservice='+p['name'])
    uids=check_pods(pods['items'],build['tag'],build['config_digest'],'kserve-container');save(c,'receiver-pods.json',pods)
    receiver=c.service_url(p['name']+'-predictor',NAMESPACE)
    broker=c.get('get','broker',p['broker'],'-n',NAMESPACE);url=urllib.parse.urlsplit(broker['status']['address']['url']);parts=url.hostname.split('.')
    if len(parts)!=5 or parts[2:]!=['svc','cluster','local'] or parts[1]!='knative-eventing':raise ValueError('Unexpected broker address')
    publish=c.service_url(parts[0],parts[1])+url.path
    def http(target,body,headers):
        'Send a bounded CloudEvent request and retain HTTP error responses for verification.'
        try:r=urllib.request.urlopen(urllib.request.Request(target,data=json.dumps(body,separators=(',',':')).encode(),headers=headers),timeout=20)
        except urllib.error.HTTPError as e:r=e
        with r:return r.status,r.read(1_048_577)
    def ready():
        'Require the actual drift event receiver to report readiness.'
        code,value=SHARED.http(receiver,'/health/ready')
        if code!=200:raise ValueError('Receiver is not ready')
        return value
    def metric(name):
        'Save the actual receiver metrics for before-and-after counter checks.'
        raw=SHARED.http_text(receiver,'/telemetry/metrics');(c.out/name).write_text(raw);return raw
    initial=ready();save(c,'ready-before.json',initial)
    if initial['stored_windows']!=0:raise ValueError('Use a fresh build-owned ledger')
    # Real historical synthetic feature observations; their simulated time is not the transport wall clock.
    window=read(c.deploy/'outputs/api-observability-28b-v4/workload/run/drift-window.json')
    window.update(reference_id=initial['reference_id'],window_id=c.release+'-window');save(c,'window.json',window)
    metric('metrics-before.prom');rows=[]
    def send(identity,value,kind=None):
        'Publish one declared event and preserve its acknowledgement before accepting it.'
        headers={**event_headers(identity),'Host':url.hostname}
        if kind:headers['ce-type']=kind
        code,raw=http(publish,value,headers);rows.append({'event_id':identity,'type':headers['ce-type'],'status':code,'response_utf8':raw.decode(),'window_id':value['window_id'],'sent_utc':datetime.now(timezone.utc).isoformat()});save(c,'publisher.json',rows)
        if code not in [200,202,204] or raw:raise ValueError('Broker did not acknowledge the event')
    send('ignored-'+c.release,{**window,'window_id':c.release+'-ignored'},'dev.edai.iot.ignored');time.sleep(3)
    save(c,'ready-after-filter.json',ready())
    if ready()['stored_windows']!=0:raise ValueError('Trigger filter leaked an unrelated event')
    for i in range(9):send(c.release+'-transport-'+str(i),window)
    for _ in range(50):
        if ready()['stored_windows']==1:break
        time.sleep(.2)
    else:raise ValueError('No persistent subscriber result')
    time.sleep(2);after=metric('metrics-after-delivery.prom');save(c,'ready-after-delivery.json',ready())
    import re
    counts=[float(v) for attributes,v in re.findall(r'^http_server_duration_milliseconds_count\{([^\n]+)\} ([0-9.e+]+)$',after,re.M)
            if 'http_target="/v1/events/drift"' in attributes and 'http_status_code="204"' in attributes]
    if sum(counts)!=9:raise ValueError('Require nine actual successful subscriber requests before direct replay')
    with sqlite3.connect('file:'+p['ledger']+'/windows.db?mode=ro',uri=True) as db:records=db.execute('SELECT reference,id,request_hash,response FROM windows ORDER BY reference,id').fetchall()
    save(c,'sqlite-readonly.json',records)
    if len(records)!=1 or records[0][1]!=window['window_id']:raise ValueError('Delivery did not deduplicate the window')
    code,response=SHARED.http(receiver,'/v1/drift:compare',window);save(c,'direct-replay.json',{'status':code,'response':response})
    if code!=200 or response!=json.loads(records[0][3]) or ready()['stored_windows']!=1:raise ValueError('Direct replay differs')
    changed=json.loads(json.dumps(window));changed['observations'][0]['features']['current_mean_10m']+=1
    code,response=SHARED.http(receiver,'/v1/events/drift',changed,event_headers('conflicting-'+c.release));save(c,'conflict.json',{'status':code,'response':response})
    if code!=409 or ready()['stored_windows']!=1:raise ValueError('Conflicting window must not overwrite stored data')
    metric('metrics-after-checks.prom')
    save(c,'verification.json',{'passed':True,'commit':c.commit,'release':c.release,'checked_utc':datetime.now(timezone.utc).isoformat(),
        'eventing_version':'knative-v1.24.0','model_pin':p['model_pin'],'reference_lineage':p['reference_lineage'],'pod_uids':uids,
        'matching_events':9,'successful_subscriber_requests':int(sum(counts)),'unique_windows':1,'filtered_events':1,'filter_observation_seconds':3,
        'scope':'Private Jenkins test/build/auto-deploy Broker→Trigger→KServe drift receiver. Nine distinct transport IDs, one persistent data-window. Production model pulled and schema/data baseline checked; no model promotion, ground-truth quality, durable broker or exactly-once transport claim.'})


def run(phase):
    """Run each phase once; preserve failed releases for diagnosis rather than deleting retained ledgers."""
    c=Context()
    if phase=='admission':
        proof=SHARED.real_admission(c,5400);c.directory.mkdir(exist_ok=False);c.out.mkdir();c.out.chmod(0o1777);save(c,'admission.json',proof);return
    if phase!='archive':
        with (c.out/(phase+'.attempt')).open('x') as f:f.write(datetime.now(timezone.utc).isoformat())
        if not phase.endswith('-host'):SHARED.real_admission(c)
    if phase=='select':
        c.log('controller-build',['docker','build','-f',str(ROOT/'infra/ci/ServingController.Dockerfile'),'-t','iot-ci-eventing-controller:'+c.release,str(ROOT)])
        save(c,'controller.json',{'id':subprocess.check_output(['docker','image','inspect','iot-ci-eventing-controller:'+c.release,'--format','{{.Id}}'],text=True).strip()})
        server=json.loads(subprocess.check_output(['docker','inspect','iot-mlflow-tracking-1']))[0]
        c.control('select-host',cluster=True,extra=['--add-host','tracking:'+registry_address(server)])
        c.bundle.mkdir(parents=True,exist_ok=False)
        for name in ['portable.json','lineage.json','model-pin.json']:__import__('shutil').copyfile(c.directory/'bundle'/name,c.bundle/name)
        c.selected()
    elif phase=='select-host':
        from mlflow.tracking import MlflowClient
        from ml.serving_release import export
        from services.predictor.runtime import PumpPredictor
        bundle=c.directory/'bundle';export(MlflowClient(tracking_uri='http://tracking:5000'),c.version,bundle,ROOT/'ml/protocols/medium-60d-v1.json')
        subprocess.run([sys.executable,str(ROOT/'scripts/write-model-pin.py'),'--bundle',str(bundle),'--output',str(bundle/'model-pin.json')],check=True);bundle.chmod(0o755)
        pin=PumpPredictor(bundle).pin;reference=c.get('get','configmap','iot-drift-reference-v1','-n','iot-features');raw=reference['data']['reference.json'];(c.out/'reference.json').write_text(raw)
        namespace=c.get('get','namespace',NAMESPACE);node=c.get('get','nodes')['items'][0]['metadata']['labels']['kubernetes.io/hostname']
        plan(c.release,'iot-coursework-drift-api:'+c.release,node,raw,pin,read(bundle/'portable.json'),str(c.directory/'runtime/eventing'/c.release))
        save(c,'selection.json',{'commit':c.commit,'release':c.release,'pin':pin,'bundle_sha256':{n:SHARED.digest(bundle/n) for n in ['portable.json','lineage.json','model-pin.json']}})
        save(c,'inventory.json',{'commit':c.commit,'node':node,'namespace_uid':namespace['metadata']['uid'],'reference_uid':reference['metadata']['uid'],'reference_sha256':sha256(raw.encode()).hexdigest(),'parent':'iot-coursework-drift-api:observe-jenkins-2'})
    elif phase=='test':
        c.selected();c.log('contracts',[sys.executable,'-m','unittest','tests.test_drift_eventing','-q'])
        base='iot-ci-observability-tests-base:'+c.release;tag='iot-ci-eventing-tests:'+c.release
        c.log('tests-base-build',['docker','build','-f',str(ROOT/'infra/ci/ServingTests.Dockerfile'),'-t',base,str(ROOT)])
        base_id=subprocess.check_output(['docker','image','inspect',base,'--format','{{.Id}}'],text=True).strip()
        c.log('tests-overlay-build',['docker','build','--build-arg',local_base(base,base_id),'-f',str(ROOT/'infra/observability/Tests.Dockerfile'),'-t',tag,str(ROOT)])
        if subprocess.check_output(['docker','image','inspect',base,'--format','{{.Id}}'],text=True).strip()!=base_id:raise ValueError('Test base changed')
        folder=c.out/'tests';folder.mkdir();folder.chmod(0o1777);scratch=Path('/srv/iot-fast/ci')/c.release;scratch.mkdir(exist_ok=False);scratch.chmod(0o1777)
        command=['docker','run','--name',c.release+'-tests','--network','none','--memory','4g','--memory-swap','4g','--cpus','2','--pids-limit','256','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--mount',f'type=bind,src={c.host_root},dst=/sealed,readonly','--mount',f'type=bind,src={c.deploy}/data/ci/source/{c.commit}/manifest.json,dst=/manifest.json,readonly',
            '--mount',f'type=bind,src={scratch},dst=/scratch','--mount',f'type=bind,src={folder},dst=/results',tag,
            '/sealed/scripts/test-drift-eventing.py','--source','/sealed','--manifest','/manifest.json','--workspace','/scratch/run','--output','/results/gate']
        c.container('tests',command,memory='4g',cpus='2');gate(c)
    elif phase=='build':
        gate(c);c.selected();parent=read(c.out/'inventory.json')['parent'];before=subprocess.check_output(['docker','image','inspect',parent,'--format','{{.Id}}'],text=True).strip()
        paths={n:'/app/'+n for n in c.seal['files'] if n.startswith('services/drift_api/') and n.endswith('.py') and n not in GATE.FILES};paths['services/observed.py']='/app/services/observed.py'
        if probe(parent,paths)!={n:c.seal['files'][n] for n in paths}:raise ValueError('Untouched parent code differs')
        tag='iot-coursework-drift-api:'+c.release;c.log('image-build',['docker','build','--build-arg',local_base(parent,before),'-f',str(ROOT/'infra/eventing/API.Dockerfile'),'-t',tag,str(ROOT)])
        if subprocess.check_output(['docker','image','inspect',parent,'--format','{{.Id}}'],text=True).strip()!=before:raise ValueError('Application parent changed')
        for n in GATE.FILES:paths[n]='/app/'+n
        expected={n:c.seal['files'][n] for n in paths}
        for n,h in c.selected()['bundle_sha256'].items():paths[n]='/app/production/'+n;expected[n]=h
        if probe(tag,paths)!=expected:raise ValueError('Packaged source or production model differs')
        c.log('image-pip-check',['docker','run','--rm','--network','none','--entrypoint','python',tag,'-m','pip','check'])
        archive=Path('/srv/iot-fast/ci')/c.release/'image.tar';c.log('image-export',['docker','save','-o',str(archive),tag])
        with archive.open('rb') as f:config=docker_config_digest(f)
        controller=read(c.out/'controller.json')['id']
        native=['docker','run','--rm','--network','none','--memory','1g','--cpus','1',
            '--mount','type=bind,src=/usr/local/bin/k3s,dst=/usr/local/bin/k3s,readonly',
            '--mount','type=bind,src=/run/k3s/containerd/containerd.sock,dst=/run/k3s/containerd/containerd.sock',
            '--entrypoint','/usr/local/bin/k3s',controller]
        exporter=subprocess.Popen(['docker','save',tag],stdout=subprocess.PIPE)
        try:subprocess.run(native[:2]+['-i']+native[2:]+['ctr','--address','/run/k3s/containerd/containerd.sock','--namespace','k8s.io','images','import','-'],stdin=exporter.stdout,check=True,timeout=240)
        finally:exporter.stdout.close();status=exporter.wait()
        if status:raise RuntimeError('CRI import stream failed')
        cri=json.loads(subprocess.check_output(native+['crictl','--config=/dev/null','--runtime-endpoint=unix:///run/k3s/containerd/containerd.sock','--image-endpoint=unix:///run/k3s/containerd/containerd.sock','inspecti',tag]))
        if cri['status']['id']!=config:raise ValueError('Imported image config differs')
        save(c,'build.json',{'commit':c.commit,'release':c.release,'tag':tag,'parent':parent,'parent_id':before,'config_digest':config,
            'image_id':subprocess.check_output(['docker','image','inspect',tag,'--format','{{.Id}}'],text=True).strip(),
            'cri_config_id':cri['status']['id'],'packaged_sha256':expected,'archive_bytes':archive.stat().st_size});archive.unlink()
    elif phase in ['deploy','verify']:c.control(phase+'-host',cluster=True,memory='4g')
    elif phase=='deploy-host':deploy(c)
    elif phase=='verify-host':verify(c)
    elif phase=='archive':
        destination=ROOT/'outputs/ci-drift-eventing';destination.mkdir(parents=True,exist_ok=False)
        __import__('shutil').copytree(c.out,destination/'native',ignore=__import__('shutil').ignore_patterns('*.attempt'))
        print(json.dumps({'commit':c.commit,'release':c.release,'archived':True}))
    else:raise ValueError('Unknown CI phase')


if __name__=='__main__':run(sys.argv[1])
