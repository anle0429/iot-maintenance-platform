"""Run one reproducible learner demo through the protected UI gateway, retaining actual child/tool evidence."""
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import argparse,base64,http.client,json,socket,ssl,subprocess,sys,time


PRODUCTION = {'iot-retrieval', 'iot-drift', 'iot-coordinator'}


def production_agents(items):
    """Select exactly the three production agents while preserving unrelated experimental resources."""
    selected = [item for item in items if item.get('metadata', {}).get('name') in PRODUCTION]
    if len(selected) != 3 or {v['metadata']['name'] for v in selected} != PRODUCTION:
        raise ValueError('Require exactly one of each production agent')
    return selected


def run(args):
    """Use the unchanged accepted agent source and genuine feature lease, with no direct-controller bypass for any chat request."""
    source=args.source.resolve();sys.path.insert(0,str(source))
    from agents.ci_contract import validate_source
    from agents.release_ci import feature_lease,ready_agents
    from agents.probe import QUESTIONS
    from agents.delegation_acceptance import verify_coordinator
    seal=json.loads((source.parent/'manifest.json').read_bytes());source_digest=validate_source(source,seal,seal['commit'])
    root=args.runtime_root.resolve();out=args.output.resolve()
    if not out.is_relative_to(root/'outputs'):raise ValueError('Keep original conversation outputs inside the retained lab')
    out.mkdir(parents=True,exist_ok=False);k=['/usr/local/bin/k3s','kubectl']
    def get(*values):
        """Read actual current readiness and feature context without collecting Secrets."""
        return json.loads(subprocess.check_output(k+list(values)+['-o','json'],stderr=subprocess.PIPE,timeout=30))
    def save(name,value):
        """Keep strict JSON acceptance and transport facts separate from original response bytes."""
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    agents=get('get','sandboxagents','-n','iot-agents');ready_agents(production_agents(agents['items']),json.loads((source/'agents/response-policy.json').read_bytes()))
    save('agents-before.json',agents)
    for role in ['retrieval', 'drift']:
        name = 'iot-'+role+'-mcp'
        backend = get('get', 'deployment', name, '-n', 'iot-agents'); save(role+'-mcp-before.json', backend)
        assert backend['status']['observedGeneration'] == backend['metadata']['generation']
        assert backend['status'].get('availableReplicas', 0) == backend['status'].get('updatedReplicas', 0) == backend['spec']['replicas'] == 2
        slices = get('get', 'endpointslices', '-n', 'iot-agents', '-l', 'kubernetes.io/service-name='+name)
        save(role+'-endpoints-before.json', slices)
        assert sum(v.get('conditions', {}).get('ready') is True for item in slices['items'] for v in item['endpoints']) == 2
    deployment=get('get','deployment','iot-feature-api','-n','iot-features')
    seed=next(v['configMap']['name'] for v in deployment['spec']['template']['spec']['volumes'] if v['name']=='seed')
    seed=get('get','configmap',seed,'-n','iot-features');context=json.loads(seed['data']['context.json'])
    lease=feature_lease(context,datetime.now(timezone.utc),minimum_seconds=300);save('feature-lease.json',{'seed_uid':seed['metadata']['uid'],**lease})
    gpu=json.loads((root/'data/runtime/ci/gpu-session.json').read_bytes());gpu_stop=datetime.fromisoformat(gpu['last_start_utc'])+timedelta(seconds=gpu['max_duration_seconds'])
    assert gpu['max_duration_seconds']==28800 and gpu['termination_action']=='STOP' and gpu['status']=='RUNNING'
    assert gpu_stop-datetime.now(timezone.utc)>timedelta(minutes=10);save('gpu-session.json',gpu)
    address=get('get','service','iot-gateway','-n','iot-gateway')['spec']['clusterIP'];tls=ssl.create_default_context(cafile=str(args.deployment/'ca.crt'))
    expected_leaf=sha256(ssl.PEM_cert_to_DER_cert((args.deployment/'server.crt').read_text())).hexdigest()
    credential=json.loads(args.credentials.read_bytes());auth='Basic '+base64.b64encode((credential['username']+':'+credential['password']).encode()).decode()
    def request(path,name,payload=None,timeout=30):
        """Keep one real original gateway response and its verified TLS identity; never follow a redirect carrying Basic auth."""
        peer=tls.wrap_socket(socket.create_connection((address,443),timeout=15),server_hostname='agents.iot.test')
        leaf=sha256(peer.getpeercert(binary_form=True)).hexdigest();assert leaf==expected_leaf
        connection=http.client.HTTPSConnection(address,443,context=tls,timeout=timeout);connection.sock=peer;peer.settimeout(timeout)
        headers={'Host':'agents.iot.test','Authorization':auth,'Accept':'application/json','A2A-Version':'0.3'}
        raw=None
        if payload is not None:raw=json.dumps(payload,indent=2).encode();headers['Content-Type']='application/json';(out/(name+'-request.json')).write_bytes(raw)
        started=time.monotonic();connection.request('POST' if raw else 'GET',path,body=raw,headers=headers)
        response=connection.getresponse();body=response.read(4194305);assert len(body)<=4194304
        (out/(name+'-response.json')).write_bytes(body)
        save(name+'-transport.json',{'path':path,'method':'POST' if raw else 'GET','status':response.status,'elapsed_seconds':time.monotonic()-started,
            'response_bytes':len(body),'ca_verified':True,'hostname_verified':True,'tls_version':peer.version(),'leaf_sha256':leaf,
            'gateway_host':'agents.iot.test','upstream_bypassed':False})
        connection.close();assert response.status==200,(name,response.status)
        return json.loads(body)
    identity=request('/api/me','identity');assert credential['username']=='coursework' and identity['sub']=='admin@kagent.dev'
    base='/api/a2a-sandboxes/iot-agents/iot-coordinator';card=request(base+'/.well-known/agent-card.json','coordinator-card')
    assert card['name']=='iot_coordinator'
    payload={'jsonrpc':'2.0','id':str(uuid4()),'method':'message/send','params':{
        'message':{'role':'user','messageId':str(uuid4()),'contextId':str(uuid4()),'parts':[{'kind':'text','text':QUESTIONS['coordinator']}]},
        'configuration':{'blocking':True}}}
    reply=request(base,'coordinator',payload,timeout=240);children={}
    for message in reply.get('result',{}).get('history',[]):
        for part in message.get('parts',[]):
            if part.get('metadata',{}).get('adk_type')!='function_response':continue
            data=part['data'];role={'iot_agents__NS__iot_retrieval':'retrieval','iot_agents__NS__iot_drift':'drift'}.get(data['name'])
            if not role:continue
            session=data['response']['subagent_session_id'];children[role]={}
            for kind,suffix in [('session',''),('tasks','/tasks')]:children[role][kind]=request('/api/sessions/'+session+suffix,role+'-'+kind)
    accepted=verify_coordinator(reply,children,knowledge_root=source/'knowledge');save('acceptance.json',accepted)
    assert datetime.now(timezone.utc)<datetime.fromisoformat(lease['lease_expires_utc'])
    after = get('get', 'sandboxagents', '-n', 'iot-agents'); save('agents-after.json', after)
    before_by_uid = {v['metadata']['uid']: v for v in agents['items']}
    assert set(before_by_uid) == {v['metadata']['uid'] for v in after['items']}
    for item in after['items']: assert item['spec'] == before_by_uid[item['metadata']['uid']]['spec']
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':seal['commit'],'source_digest':source_digest,
        'runner_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),'gateway_host':'agents.iot.test','leaf_sha256':expected_leaf,
        'feature_seed_uid':seed['metadata']['uid'],'actual_acceptance':accepted,'all_chat_requests_through_https_ui_gateway':True,
        'selected_production_agents':sorted(PRODUCTION),'experimental_agents_preserved':len(agents['items'])-3,
        'scope':'One complete GPU coordinator response, two independent real specialist histories and four MCP calls through the authenticated private UI gateway. Not a load benchmark, multi-tenant authorization or full coursework completion.'}
    save('verification.json',report)
    variants=[credential['password'].encode(),auth.encode(),base64.b64encode((credential['username']+':'+credential['password']).encode())]
    for path in out.iterdir():
        if path.is_file():assert not any(value in path.read_bytes() for value in variants),'Private credential found in a chat receipt'
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','source','deployment','credentials','output']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
