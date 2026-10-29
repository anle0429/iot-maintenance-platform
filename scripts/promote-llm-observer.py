"""Validate a private Envoy trace bridge, promote the accepted observer and enable content-free native Go agent tracing with guarded rollback."""
from datetime import datetime,timedelta,timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import Request,ProxyHandler,build_opener
import argparse,base64,json,os,re,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from infra.llm_observer_routing import TRACE_ENV,TRACE_PATH,controller_trace_environment,private_trace_route


def run(args):
    """Change routing only after native canary acceptance and actual validation by the currently deployed Envoy binary."""
    import yaml
    os.umask(0o077);root=args.runtime_root.resolve();out=args.output.resolve();canary=args.canary.resolve();probe=args.probe.resolve()
    assert os.geteuid()==0 and root.is_relative_to('/srv/iot-data')
    assert all(p.is_relative_to(root/'outputs') for p in [out,canary,probe]) and re.fullmatch(r'llm-observer-rollout-[a-z0-9-]+',out.name)
    seal=json.loads((ROOT.parent/'manifest.json').read_bytes());assert seal['format']=='iot-llm-observer-rollout-v1'
    for n,digest in seal['files'].items():assert sha256((ROOT/n).read_bytes()).hexdigest()==digest,n
    accepted=json.loads((canary/'verification.json').read_bytes());tested=json.loads((probe/'verification.json').read_bytes())
    assert accepted['passed'] and tested['passed'] and accepted['pod_uid']==tested['pod_uid'] and tested['request_rows']==9
    for n,digest in accepted['packaged_files'].items():assert sha256((ROOT/n).read_bytes()).hexdigest()==digest
    session=json.loads((root/'data/runtime/ci/session.json').read_bytes())
    assert datetime.fromisoformat(session['auto_stop_utc'])>datetime.now(timezone.utc)+timedelta(minutes=30)
    assert not subprocess.check_output(['systemctl','list-units','--state=running','--no-legend','iot-agent-chat-*']).strip()
    out.mkdir(exist_ok=False);k=['/usr/local/bin/k3s','kubectl'];changed=[];opener=build_opener(ProxyHandler({}))
    def save(name,value):
        """Retain approved original resource and verification facts without any Secret body."""
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    def command(label,argv,payload=None,timeout=60):
        """Record bounded native command outcomes while keeping private credential-bearing stdin out of receipts."""
        p=subprocess.run(argv,input=payload,capture_output=True,timeout=timeout)
        (out/(label+'.stdout')).write_bytes(p.stdout);(out/(label+'.stderr')).write_bytes(p.stderr);(out/(label+'.exit')).write_text(str(p.returncode)+'\n')
        if p.returncode:raise RuntimeError(label+' failed')
        return p.stdout
    def get(*query):
        """Read one named private object or explicit label selection."""
        return json.loads(subprocess.check_output(k+['get',*query,'-o','json'],stderr=subprocess.PIPE,timeout=30))
    def absent(kind,name):
        """Refuse existing owned resource names rather than replacing evidence from a prior rollout."""
        assert not subprocess.check_output(k+['get',kind,name,'-n','iot-llm','--ignore-not-found','-o','name'],stderr=subprocess.PIPE).strip()
    def patch(label,kind,name,namespace,before,after):
        """Compare UID and full observed specification before changing only the planned specification."""
        operations=[{'op':'test','path':'/metadata/uid','value':before['metadata']['uid']},
                    {'op':'test','path':'/spec','value':before['spec']},{'op':'replace','path':'/spec','value':after}]
        save(label+'-patch.json',operations)
        # Record intent first so a timeout cannot hide an already applied mutation.
        changed.append((label,kind,name,namespace,before,after))
        command(label,k+['patch',kind,name,'-n',namespace,'--type=json','--patch-file=/dev/stdin'],json.dumps(operations).encode())
    objects={name:get(*query) for name,query in {
        'pool':['inferencepool.inference.networking.k8s.io','iot-router','-n','iot-llm'],
        'router':['deployment','iot-router-epp','-n','iot-llm'],'controller':['deployment','kagent-controller','-n','iot-agents'],
        'control':['deployment','iot-model-control','-n','iot-llm'],'gpu':['deployment','iot-model-gpu','-n','iot-llm'],
        'model':['modelconfig','default-model-config','-n','iot-agents'],'agents':['sandboxagents','-n','iot-agents'],
        'templates':['actortemplates','-n','iot-agents'],'envoy':['configmap','envoy','-n','iot-llm']}.items()}
    save('source.json',seal)
    for name,value in objects.items():save('before-'+name+'.json',value)
    for name in ['pool','router','control','gpu','model']:
        old=json.loads((canary/('after-'+name+'.json')).read_bytes())
        assert old['metadata']['uid']==objects[name]['metadata']['uid'] and old['spec']==objects[name]['spec'],name
    agents={v['metadata']['uid']:v['spec'] for v in objects['agents']['items']}
    assert agents=={v['metadata']['uid']:v['spec'] for v in json.loads((canary/'after-agents.json').read_bytes())['items']}
    pods=get('pods','-n','iot-llm','-l','app.kubernetes.io/instance=iot-llm-observer');save('observer-pods-before.json',pods)
    assert len(pods['items'])==1 and pods['items'][0]['metadata']['uid']==accepted['pod_uid'] and all(v['ready'] for v in pods['items'][0]['status']['containerStatuses'])
    oldconfig=yaml.safe_load(objects['envoy']['data']['envoy.yaml']);planned=private_trace_route(oldconfig)
    suffix=out.name.removeprefix('llm-observer-rollout-');cm='iot-llm-router-observe-'+suffix;job='iot-llm-envoy-validate-'+suffix
    absent('configmap',cm);absent('job',job)
    newcm={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':cm,'namespace':'iot-llm',
        'annotations':{'iot.coursework/source-commit':seal['commit'],'iot.coursework/previous-config-uid':objects['envoy']['metadata']['uid']}},
        'immutable':True,'data':{'envoy.yaml':json.dumps(planned,indent=2)+'\n'}}
    save('envoy-config-map.json',newcm);command('create-config',k+['create','-f','-'],json.dumps(newcm).encode())
    routerpods=get('pods','-n','iot-llm','-l','llm-d-router-gateway=iot-router-epp');save('router-pods-before.json',routerpods)
    ready=[p for p in routerpods['items'] if not p['metadata'].get('deletionTimestamp') and p['status']['phase']=='Running' and all(c['ready'] for c in p['status']['containerStatuses'])]
    assert len(ready)==1
    image=next(c['imageID'].removeprefix('docker-pullable://') for c in ready[0]['status']['containerStatuses'] if c['name']=='envoy-proxy')
    assert re.fullmatch(r'(?:docker.io/)?envoyproxy/envoy@sha256:[0-9a-f]{64}',image)
    validation={'apiVersion':'batch/v1','kind':'Job','metadata':{'name':job,'namespace':'iot-llm'},
        'spec':{'backoffLimit':0,'activeDeadlineSeconds':90,'template':{'spec':{
            'restartPolicy':'Never','automountServiceAccountToken':False,'nodeSelector':{'kubernetes.io/hostname':'iot-cw-vm'},
            'securityContext':{'runAsNonRoot':True,'runAsUser':10001,'runAsGroup':10001,'seccompProfile':{'type':'RuntimeDefault'}},
            'containers':[{'name':'envoy','image':image,'imagePullPolicy':'Never','command':['envoy'],
                'args':['--mode','validate','-c','/etc/envoy/envoy.yaml'],
                'securityContext':{'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}},
                'resources':{'requests':{'cpu':'50m','memory':'32Mi'},'limits':{'cpu':'1','memory':'256Mi'}},
                'volumeMounts':[{'name':'config','mountPath':'/etc/envoy','readOnly':True}]}],
            'volumes':[{'name':'config','configMap':{'name':cm}}]}}}}
    save('validation-job.json',validation);command('create-validator',k+['create','-f','-'],json.dumps(validation).encode())
    command('wait-validator',k+['wait','--for=condition=complete','job/'+job,'-n','iot-llm','--timeout=110s'],timeout=120)
    validationpods=get('pods','-n','iot-llm','-l','job-name='+job);save('validation-pods.json',validationpods)
    assert len(validationpods['items'])==1 and validationpods['items'][0]['status']['containerStatuses'][0]['state']['terminated']['exitCode']==0
    command('validator-logs',k+['logs','job/'+job,'-n','iot-llm'])
    token=base64.b64decode(get('secret','iot-llm-observer-client','-n','iot-llm')['data']['API_KEY']).decode()
    def request(label,path,payload,authenticated=True,content_type='application/json'):
        """Keep the original fixed administrative request/response bytes without retaining authentication headers."""
        service=get('service','iot-router-epp','-n','iot-llm');headers={'Content-Type':content_type}
        assert service['spec']['type']=='ClusterIP'
        if authenticated:headers['Authorization']='Bearer '+token
        (out/(label+'-request.bin')).write_bytes(payload)
        with opener.open(Request('http://'+service['spec']['clusterIP']+':8081'+path,data=payload,headers=headers),timeout=150) as response:
            body=response.read(2*1024**2+1);assert len(body)<=2*1024**2
            save(label+'-http.json',{'status':response.status,'headers':{k.lower():v for k,v in response.headers.items()}})
        (out/(label+'-response.bin')).write_bytes(body);return body
    phase='router'
    try:
        router=json.loads(json.dumps(objects['router']['spec']))
        volume=next(v for v in router['template']['spec']['volumes'] if v['name']=='config');assert volume['configMap']['name']=='envoy';volume['configMap']['name']=cm
        patch('router-patch','deployment','iot-router-epp','iot-llm',objects['router'],router)
        command('router-ready',k+['rollout','status','deployment/iot-router-epp','-n','iot-llm','--timeout=180s'],timeout=190)
        phase='pool';pool=json.loads(json.dumps(objects['pool']['spec']))
        assert pool['targetPorts']==[{'number':8080}]
        pool['selector']['matchLabels']={'app.kubernetes.io/instance':'iot-llm-observer','app.kubernetes.io/name':'iot-llm-observer'}
        pool['targetPorts']=[{'number':8081}]
        patch('pool-patch','inferencepool.inference.networking.k8s.io','iot-router','iot-llm',objects['pool'],pool)
        time.sleep(3)
        body={'model':'iot-qwen2.5-7b','stream':False,'max_tokens':48,'temperature':0,'seed':7,
            'messages':[{'role':'user','content':'In one sentence, why should a pump prediction identify its feature timestamp?'}]}
        reply=json.loads(request('routed-generation','/v1/chat/completions',json.dumps(body).encode()))
        assert reply['choices'][0]['finish_reason']=='stop' and reply['usage']['total_tokens']>0
        request('routed-trace-empty',TRACE_PATH,b'',False,'application/x-protobuf')
        phase='controller';controller=json.loads(json.dumps(objects['controller']['spec']))
        containers=controller['template']['spec']['containers'];assert len(containers)==1 and containers[0]['image']=='ghcr.io/kagent-dev/kagent/controller:0.10.3'
        containers[0]['env']=controller_trace_environment(containers[0]['env'])
        patch('controller-patch','deployment','kagent-controller','iot-agents',objects['controller'],controller)
        command('controller-ready',k+['rollout','status','deployment/kagent-controller','-n','iot-agents','--timeout=180s'],timeout=190)
        for name,namespace in [('router','iot-llm'),('controller','iot-agents'),('control','iot-llm'),('gpu','iot-llm')]:
            resource={'router':'iot-router-epp','controller':'kagent-controller','control':'iot-model-control','gpu':'iot-model-gpu'}[name]
            current=get('deployment',resource,'-n',namespace);save('after-'+name+'.json',current)
            expected={'router':router,'controller':controller}.get(name,objects[name]['spec'])
            assert current['metadata']['uid']==objects[name]['metadata']['uid'] and current['spec']==expected
        current=get('sandboxagents','-n','iot-agents');save('after-agents.json',current)
        assert agents=={v['metadata']['uid']:v['spec'] for v in current['items']}
        save('after-templates.json',get('actortemplates','-n','iot-agents'));save('after-pool.json',get('inferencepool.inference.networking.k8s.io','iot-router','-n','iot-llm'))
        model=get('modelconfig','default-model-config','-n','iot-agents');save('after-model.json',model)
        assert model['metadata']['uid']==objects['model']['metadata']['uid'] and model['spec']==objects['model']['spec']
        report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':seal['commit'],'tested_source_commit':accepted['tested_source_commit'],
            'observer_pod_uid':accepted['pod_uid'],'new_immutable_config_map':cm,'validated_envoy_image':image,'trace_environment':TRACE_ENV,
            'routing_changed':True,'original_agent_business_specs_unchanged':True,'original_control_and_gpu_specs_unchanged':True,
            'scope':'Actual immutable Envoy validation, private trace bridge, observer pool selection, one real routed GPU response and controller trace configuration. Fresh agent executions, complete trace readbacks, monitoring, load and UI captures remain separate checks.'}
    except BaseException as error:
        rollback=[]
        for label,kind,name,namespace,before,after in reversed(changed):
            current=get(kind,name,'-n',namespace)
            if current['metadata']['uid']!=before['metadata']['uid'] or current['spec'] not in [before['spec'],after]:
                rollback.append({'name':name,'restored':False,'reason':'ownership_or_spec_conflict'});continue
            if current['spec']==before['spec']:rollback.append({'name':name,'restored':True,'already_original':True});continue
            operations=[{'op':'test','path':'/metadata/uid','value':before['metadata']['uid']},{'op':'test','path':'/spec','value':after},
                        {'op':'replace','path':'/spec','value':before['spec']}]
            try:
                command('rollback-'+label,k+['patch',kind,name,'-n',namespace,'--type=json','--patch-file=/dev/stdin'],json.dumps(operations).encode())
                if kind=='deployment':command('rollback-ready-'+label,k+['rollout','status','deployment/'+name,'-n',namespace,'--timeout=180s'],timeout=190)
                rollback.append({'name':name,'restored':True})
            except Exception as failure:rollback.append({'name':name,'restored':False,'error_type':type(failure).__name__})
        save('verification.json',{'passed':False,'phase':phase,'error_type':type(error).__name__,'error':str(error),'rollback':rollback});raise
    save('verification.json',report)
    variants=[token.encode(),base64.b64encode(token.encode())]
    for p in out.iterdir():
        if p.is_file():assert not any(v in p.read_bytes() for v in variants),'Credential-bearing rollout evidence refused'
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output','canary','probe']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
