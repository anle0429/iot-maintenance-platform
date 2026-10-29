"""Recompute the private agent gateway, scoped Vault consumers and actual delegated GPU conversation from original receipts."""
from collections import Counter
from datetime import datetime,timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
import json,ssl,sys,tarfile
from integration_evidence import ROOT,capsule,read

sys.path.insert(0,str(ROOT))
from infra.gateway.agents import access_resources,controller_patch,ingresses
from infra.gateway.ui_identity import ui_identity_patch
from agents.delegation_acceptance import verify_coordinator

MANIFEST_SHA256='9035f2e28d65872c7b84351da3b982e18d1a38272825568173a123ce3c07133b'
LEAF='e60801a16f3febb05fb3d855b627d7acf43af8df8fde5dd3e7771d1190e8a999'
COMMIT='adc15dc2f6903d4231bbe19df2172cf5523a807f'


def verify():
    """Require original inventories, phase-specific sources, native positive/negative controls and unchanged business acceptance."""
    base,count=capsule('agent-gateway',MANIFEST_SHA256);meta=read(base,'native-archive.json');raw=(base/'native-receipts.tar.gz').read_bytes()
    assert len(raw)==meta['archive_bytes'] and sha256(raw).hexdigest()==meta['archive_sha256']
    assert len(raw)==1221726 and meta['archive_sha256']=='cd7b157fb8d034b5540c11801b95b6bc2418402acf1993bdefb9d22c0f8bc5dd'
    assert meta['native_payloads']==714 and count==16
    assert meta['recompressed_or_rewritten_on_pc'] is False and meta['actual_native_credentials_scanned'] and meta['pc_actual_gpu_join_credential_scanned']
    bodies={}
    with tarfile.open(fileobj=BytesIO(raw),mode='r:gz') as packet:
        for member in packet:
            assert member.isdir() or member.isfile()
            if not member.isfile():continue
            name=Path(member.name).relative_to('agent-gateway-capture-29b').as_posix()
            assert '..' not in Path(name).parts and name not in bodies
            bodies[name]=packet.extractfile(member).read()
    inventory=json.loads(bodies['manifest.json']);assert sha256(bodies['manifest.json']).hexdigest()==meta['original_inventory_sha256']
    assert len(inventory['files'])==meta['native_payloads'] and set(bodies)==set(inventory['files'])|{'manifest.json'}
    for name,item in inventory['files'].items():assert len(bodies[name])==item['bytes'] and sha256(bodies[name]).hexdigest()==item['sha256'],name
    def original(name):
        """Read one JSON document only after authenticating every original archived byte."""
        return json.loads(bodies[name])
    def native(run,name):
        """Resolve one explicitly named native execution phase."""
        return original('native/'+run+'/'+name)
    seals={}
    for label,size in [('agent-gateway-1c5faee0',8),('agent-gateway-7c9a5856',10),('agent-gateway-5f0a8c5e',3),
                       ('agent-gateway-bcf9c436',10),('agent-gateway-final-adc15dc2',16),('accepted-agent',362)]:
        prefix='source/'+label+'/';seal=original(prefix+'manifest.json');assert len(seal['files'])==size;seals[label]=seal
        for name,digest in seal['files'].items():assert sha256(bodies[prefix+'tree/'+name]).hexdigest()==digest
    current=seals['agent-gateway-final-adc15dc2'];assert current['commit']==COMMIT and read(base,'current-source.json')==current
    for name,digest in current['files'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    accepted_source=seals['accepted-agent'];assert accepted_source['commit']=='daee5d3ff335917ffb7f1705e17bf1e723aa20ad'
    for name,digest in accepted_source['files'].items():
        if name.startswith(('agents/','knowledge/')):assert sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    for public,run in [('http-verification','agent-gateway-probe-29d'),('conversation-verification','agent-gateway-chat-29c'),
                       ('vault-verification','agent-vault-29a'),('identity-verification','agent-ui-identity-29a')]:
        assert (base/(public+'.json')).read_bytes()==bodies['native/'+run+'/verification.json']
    vault=read(base,'vault-verification.json');assert vault['passed'] and not vault['credential_values_archived']
    for name,digest in vault['source_sha256'].items():assert digest==current['files'][name]
    for kind in ['gateway','services']:
        consumer=native('agent-vault-29a','consumer-'+kind+'.json');role=native('agent-vault-29a','role-'+kind+'.json')['data']
        policy=native('agent-vault-29a','policy-'+kind+'.json')['data']['policy']
        assert policy==(ROOT/('infra/security/vault-agent-'+kind+'.hcl')).read_text(encoding='utf-8')
        assert consumer==vault['roles'][kind] and consumer['policy_sha256']==sha256(policy.encode()).hexdigest()
        assert consumer['secret_version']==1 and consumer['readback_exact'] and consumer['revoked_token_status']==403
        assert consumer['lease']=={'lease_duration':300,'renewable':True,'token_policies':['iot-agent-'+kind]}
        assert consumer['cross_service_statuses']=={'iot/data/agents/'+('services' if kind=='gateway' else 'gateway'):403,'iot/data/ci/airflow':403,'iot/data/gateway/basic':403}
        assert role['token_policies']==['iot-agent-'+kind] and role['token_no_default_policy'] and role['token_ttl']==300 and role['token_max_ttl']==900
        assert role['secret_id_num_uses']==20 and role['bind_secret_id']
    sync=native('agent-vault-29a','service-sync.json');assert sync['passed'] and not sync['credential_rotation']
    assert set(sync['secrets'])=={'iot-agent-tool-headers','iot-drift-mcp-auth','iot-local-model-client','iot-retrieval-mcp-auth'}
    assert all(row['uid'] and row['same_credential_bytes'] for row in sync['secrets'].values())
    initial='agent-gateway-29a';ready=native(initial,'ready.json');assert ready['ready'] and ready['authentication_verified'] is False
    for name,digest in ready['source_sha256'].items():assert digest==seals['agent-gateway-1c5faee0']['files'][name]
    before=native(initial,'before-gateway.json');after=native(initial,'after-gateway.json');patch=controller_patch(before,ready['image'])
    assert patch==native(initial,'controller-patch.json') and before['metadata']['uid']==after['metadata']['uid']==ready['gateway_uid']
    expected=json.loads(json.dumps(before['spec']));expected['template']['spec']['containers'][0]['args']=patch[-1]['value'];assert after['spec']==expected
    assert original('final-cluster/gateway-deployment.json')['spec']==after['spec']
    for group in ['agents','pools']:
        left=native(initial,'before-'+group+'.json')['items'];right=native(initial,'after-'+group+'.json')['items']
        assert {r['metadata']['uid']:r['spec'] for r in left}=={r['metadata']['uid']:r['spec'] for r in right}
    assert native(initial,'before-model.json')['spec']==native(initial,'after-model.json')['spec']==original('final-cluster/modelconfig.json')['spec']
    service=original('final-cluster/gateway-service.json');assert service['spec']['type']=='ClusterIP' and not service['spec'].get('externalIPs')
    for item in access_resources():
        actual=original('final-cluster/agent-gateway-'+('role' if item['kind']=='Role' else 'rolebinding')+'.json')
        for key in ['rules'] if item['kind']=='Role' else ['subjects','roleRef']:assert actual[key]==item[key]
    for expected in ingresses():
        name=expected['metadata']['name'];actual=native('agent-gateway-update-29c',name+'-after.json')
        previous=native('agent-gateway-update-29c',name+'-before.json')
        assert actual['metadata']['uid']==previous['metadata']['uid'] and actual['spec']==previous['spec']==expected['spec']
        assert all(actual['metadata']['annotations'][k]==v for k,v in expected['metadata']['annotations'].items())
        current_ingress=next(v for v in original('final-cluster/agent-ingresses.json')['items'] if v['metadata']['name']==name)
        assert current_ingress['spec']==actual['spec'] and current_ingress['metadata']['uid']==actual['metadata']['uid']
    assert sha256(ssl.PEM_cert_to_DER_cert(bodies['native/'+initial+'/server.crt'].decode())).hexdigest()==LEAF
    rejected=[e for e in native(initial,'events.json')['items'] if e.get('reason')=='Rejected'];assert len(rejected)==2
    assert all('invalid character in header value: $' in e['message'] for e in rejected)
    assert b'OK' in bodies['native/'+initial+'/tls-verify.txt']
    report=read(base,'http-verification.json');assert report['passed'] and report['source_sha256']==current['files']['scripts/probe-agent-gateway.py']
    prefix='native/agent-gateway-probe-29d/'
    def response(row,leaf=LEAF):
        """Recompute body hashes and require actual CA/hostname checks on each retained HTTPS response."""
        assert sha256(row['body'].encode()).hexdigest()==row['body_sha256']
        if row['tls'] is not None:assert row['tls']['ca_verified'] and row['tls']['hostname_verified'] and row['tls']['leaf_sha256']==leaf
    rows=original(prefix+'routing-auth.json');assert len(rows)==14
    for row in rows:response(row)
    assert Counter(r['status'] for r in rows)=={401:8,200:4,308:2}
    for host in ['agents.iot.test','registry.iot.test']:
        redirect=next(r for r in rows if r['host']==host and r['status']==308);target=urlsplit(redirect['headers']['Location'])
        assert target.scheme=='https' and target.hostname==host and target.port in {None,443} and target.path=='/'
        html=next(r for r in rows if r['host']==host and r['path']=='/' and r['status']==200);assert '<script' in html['body']
    for role in ['agents','registry']:
        asset=original(prefix+role+'-javascript.json');response(asset);assert asset['status']==200 and len(asset['body'])>100
    identity=original(prefix+'identity-override.json');response(identity);assert json.loads(identity['body'])=={'sub':'admin@kagent.dev'}
    agents=original('final-cluster/agents.json')['items']
    for role in ['retrieval','drift','coordinator']:
        row=original(prefix+'catalog-'+role+'.json');response(row);entry=json.loads(row['body']);agent=next(a for a in agents if a['metadata']['name']=='iot-'+role)
        assert row['status']==200 and entry['metadata']['tag']=='0.2.2' and entry['metadata']['annotations']['iot.coursework/resource-uid']==agent['metadata']['uid']
        digest=sha256(json.dumps(agent['spec'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        assert entry['metadata']['annotations']['iot.coursework/spec-sha256']==digest
    old=original(prefix+'old-routes.json');assert len(old)==4 and all(r['status']==200 for r in old)
    negatives=original(prefix+'tls-negative.json');assert negatives[0]['verify_code']==20 and negatives[1]['reason']=='TLSV1_UNRECOGNIZED_NAME' and all(r['rejected'] for r in negatives)
    burst=original(prefix+'burst.json');assert len(burst['responses'])==320
    for row in burst['responses']:response(row)
    counts={str(k):v for k,v in Counter(r['status'] for r in burst['responses']).items()}
    assert counts==burst['counts']==report['burst_statuses']=={'200':150,'429':170}
    assert 0<burst['duration_seconds']==report['burst_duration_seconds']<5
    recovered=original(prefix+'recovered.json');response(recovered);assert recovered['status']==200
    config=bodies[prefix+'nginx-config.txt'];assert config.count(b'rate=30r/s;')==2 and config.count(b'X-User-Id "admin@kagent.dev"')==2
    assert config.count(b'limit_req_status 429;')>=2 and config.count(b'burst=90 nodelay;')==2
    previous=native('agent-gateway-probe-29c','burst.json');assert previous['counts']=={'200':168,'429':152}
    ui='agent-ui-identity-29a';left=native(ui,'before-ui.json');right=native(ui,'after-ui.json');patch=ui_identity_patch(left)
    assert patch==native(ui,'patch.json') and left['metadata']['uid']==right['metadata']['uid']
    expected=json.loads(json.dumps(left['spec']));expected['template']['spec']['containers'][0]['env']=patch[-1]['value'];assert right['spec']==expected
    assert original('final-cluster/ui-deployment.json')['spec']==right['spec']
    for name in ['workerpools','sandboxagents']:
        assert {r['metadata']['uid']:r['spec'] for r in native(ui,'before-'+name+'.json')['items']}=={r['metadata']['uid']:r['spec'] for r in native(ui,'after-'+name+'.json')['items']}
    assert native(ui,'before-modelconfig.json')['spec']==native(ui,'after-modelconfig.json')['spec']
    chat='agent-gateway-chat-29c';proof=read(base,'conversation-verification.json');assert proof['passed'] and proof['all_chat_requests_through_https_ui_gateway']
    assert proof['runner_sha256']==current['files']['scripts/probe-agent-gateway-conversation.py']
    reply=native(chat,'coordinator-response.json');children={r:{k:native(chat,r+'-'+k+'-response.json') for k in ['session','tasks']} for r in ['retrieval','drift']}
    acceptance=verify_coordinator(reply,children,knowledge_root=ROOT/'knowledge')
    assert acceptance==proof['actual_acceptance']==native(chat,'acceptance.json') and acceptance['coordinator_delegation_verified']
    assert sum(r['successful_tool_events'] for r in acceptance['children'].values())==4 and not acceptance['coursework_complete']
    for name,body in bodies.items():
        if name.startswith('native/'+chat+'/') and name.endswith('-transport.json'):
            transport=json.loads(body);assert transport['status']==200 and transport['ca_verified'] and transport['hostname_verified'] and transport['leaf_sha256']==LEAF
            assert transport['gateway_host']=='agents.iot.test' and transport['upstream_bypassed'] is False
            response_name=name.replace('-transport.json','-response.json');assert len(bodies[response_name])==transport['response_bytes']
    feature=native('agent-feature-29l','verification.json');lease=native(chat,'feature-lease.json');gpu=native(chat,'gpu-session.json')
    assert feature['passed'] and feature['machines']==200 and feature['model_version']=='6' and lease['seed_uid']==feature['seed_uid']==proof['feature_seed_uid']
    assert native('agent-feature-29l','physical-ttl-before.json')=={'keys':0,'pttl_ms':[]}
    ttl=native('agent-feature-29l','physical-ttl-after.json');assert ttl['keys']==len(ttl['pttl_ms'])==200 and all(0<t<=3600000 for t in ttl['pttl_ms'])
    start=datetime.fromisoformat(lease['prepared_wall_utc']);end=datetime.fromisoformat(lease['lease_expires_utc']);assert (end-start).total_seconds()==3600
    assert start<datetime.fromisoformat(proof['checked_utc'])<end and lease['remaining_seconds']>=300
    assert gpu==native('gpu-gateway-session-29b','new-session.json') and gpu['max_duration_seconds']==28800 and gpu['termination_action']=='STOP'
    assert datetime.fromisoformat(gpu['last_start_utc'])+timedelta(seconds=28800)>datetime.fromisoformat(proof['checked_utc'])
    journals=[('iot-agent-gateway-probe-29a','a1f98b7c9eaf4e739dd9d0e417169b5a',1),('iot-agent-gateway-probe-29b','23b9b900f2654a95aead4f7e22dbef11',1),
              ('iot-agent-gateway-chat-29b','fe50ca0a6ba84df994ab831644eeefc7',2),('iot-agent-gateway-probe-29d','8f6f312d566f4cdf862f928f52e7fbdf',0),
              ('iot-agent-gateway-chat-29c','7271970ee8ff4f5981b6ab40ef033344',0)]
    for unit,invocation,status in journals:
        entries=[json.loads(line) for line in bodies['process/'+unit+'.journal.jsonl'].splitlines()]
        assert invocation in {e.get('_SYSTEMD_INVOCATION_ID') for e in entries}
        messages='\n'.join(e.get('MESSAGE','') for e in entries)
        assert ('status='+str(status)+'/' in messages) if status else ('Deactivated successfully' in messages)
    assert original('failed-chat-packet-path.json')['output_directory_absent'] and b'Ran 8 tests' in bodies['current-tests/stderr.txt'] and bodies['current-tests/exit.txt']==b'0\n'
    ui_index=read(base,'ui/index.json');assert ui_index['original_screenshots'] and len(ui_index['entries'])==4
    assert sha256(bodies['executed-scripts/iot-view-agent-gateway-29e.py']).hexdigest()==current['files']['scripts/view-agent-gateway.py']==ui_index['agents_viewer_source_sha256']
    for entry in ui_index['entries']:
        assert not entry['pixels_edited'] and (base/'ui'/entry['image']).read_bytes().startswith(b'\xff\xd8\xff')
        text=(base/'ui'/entry['accessibility']).read_text(encoding='utf-8');assert 'iot-coordinator' in text
        if entry['image']=='coordinator-answer-final.jpg':assert acceptance['context_id'] in text and 'risk_score' in text and 'assessment_sha256' in text and 'Ready' in text
        if entry['image']=='retrieval-tools-final.jpg':assert all(term in text for term in ['3 succeeded','get_machine_features','predict_failure','search_knowledge'])
    audit=[json.loads(line) for line in bodies['viewer-audit/29e/agents.jsonl'].splitlines()]
    assert any(acceptance['context_id'] in row['path'] and row['status']==200 for row in audit)
    assert all(r['ca_verified'] and r['hostname_verified'] and r['leaf_sha256']==LEAF and not r['body_edited'] and not r['credentials_logged'] for r in audit)
    registry_audit=[json.loads(line) for line in bodies['viewer-audit/29b/registry.jsonl'].splitlines()]
    assert any(row['path']=='/v0/agents' and row['status']==200 for row in registry_audit)
    assert all(row['ca_verified'] and row['hostname_verified'] and row['leaf_sha256']==LEAF and not row['body_edited'] for row in registry_audit)
    return {'passed':True,'public_payloads':count,'native_payloads':len(inventory['files']),'current_source_files':16,
            'native_http_statuses':counts,'unauthorized_requests_rejected':8,'vault_scoped_consumers':2,'real_tool_calls':4,
            'actual_delegation':acceptance,'original_ui_captures':4,'coursework_complete':False,
            'scope':'Private single operator gateway and original loopback UI captures; no public DNS, browser CA trust, tenant authorization or full LLM coursework claim.'}


if __name__=='__main__':print(json.dumps(verify(),indent=2))
