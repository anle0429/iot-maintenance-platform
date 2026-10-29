"""Reconcile the accepted private trace route and one genuine coordinator cohort with original per-Pod counters, model usage and Jaeger graph identities."""
from collections import Counter
from datetime import datetime,timezone
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import json,re,sys,tarfile
from integration_evidence import ROOT,capsule,read,exited
sys.path.insert(0,str(ROOT))
from infra.llm_observer_routing import TRACE_ENV,TRACE_PATH,TRACE_CLUSTER,controller_trace_environment,private_trace_route
from agents.delegation_acceptance import verify_coordinator

MANIFEST='90713b10b9e74a30bc1c3148fe85dfde711c33cac9b1c9461b96b6115d0dce7e'


def samples(raw,name):
    """Parse scalar series independently, preserving every label and refusing duplicated identities."""
    result={}
    for line in raw.decode().splitlines():
        match=re.fullmatch(re.escape(name)+r'(?:\{([^}]*)\})? ([-+0-9.eE]+)',line)
        if not match:continue
        labels=tuple(sorted(re.findall(r'([a-z_]+)="([^"\\]*)"',match[1] or '')))
        assert labels not in result;result[labels]=float(match[2])
    return result


def verify():
    """Authenticate original bytes, check the native transition and independently reconcile the observed execution cohort."""
    base,count=capsule('llm-agent-telemetry',MANIFEST);meta=read(base,'native-archive.json');raw=(base/'native-receipts.tar.gz').read_bytes()
    assert count==5 and len(raw)==meta['archive_bytes']==1230105
    assert sha256(raw).hexdigest()==meta['archive_sha256']=='12cf4aadf573cdd5c25b4a336815db0a64129416a0d6263815e0e2d8f7c4b9d4'
    assert meta['actual_native_credentials_scanned'] and meta['pc_actual_gpu_join_credential_scanned']
    assert not meta['recompressed_or_rewritten_on_pc'] and not meta['workloads_rerun_for_capture']
    bodies={}
    with tarfile.open(fileobj=BytesIO(raw)) as packet:
        for member in packet:
            assert member.isdir() or member.isfile()
            if not member.isfile():continue
            name=PurePosixPath(member.name).relative_to('llm-agent-observation-capture-29b').as_posix()
            assert '..' not in PurePosixPath(name).parts and name not in bodies and member.size<20_000_000
            bodies[name]=packet.extractfile(member).read()
    inventory=json.loads(bodies['manifest.json'])
    assert sha256(bodies['manifest.json']).hexdigest()==meta['original_inventory_sha256']=='921932400232fd3623cbef8e2b42f68421bea0d5e1f88ba2ff071b713c8baf17'
    assert len(inventory['files'])==meta['native_payloads']==2104 and set(bodies)==set(inventory['files'])|{'manifest.json'}
    for n,item in inventory['files'].items():assert len(bodies[n])==item['bytes'] and sha256(bodies[n]).hexdigest()==item['sha256'],n
    def native(name):
        """Decode an original document after authenticating every captured file."""
        return json.loads(bodies[name])
    rollout='native/llm-observer-rollout-29b/';cohort='native/llm-agent-observation-29a/';chat='native/agent-observer-chat-29a/'
    for public,n in [('rollout-verification.json',rollout+'verification.json'),('conversation-verification.json',chat+'verification.json'),
                     ('feature-lease.json',cohort+'feature-lease.json')]:assert (base/public).read_bytes()==bodies[n]
    for prefix,files in [('rollout-3c8f3c35',18),('rollout-1bc089e6',18),('accepted-agent',362)]:
        source=native('source/'+prefix+'/manifest.json');assert len(source['files'])==files
        for n,digest in source['files'].items():assert sha256(bodies['source/'+prefix+'/tree/'+n]).hexdigest()==digest,n
    for n in ['infra/llm_observer_routing.py','scripts/promote-llm-observer.py']:
        assert (ROOT/n).read_bytes()==bodies['source/rollout-1bc089e6/tree/'+n]
    for n in ['agents/answer_acceptance.py','agents/delegation_acceptance.py','agents/response-policy.json']:
        assert (ROOT/n).read_bytes()==bodies['source/accepted-agent/tree/'+n]
    for label,invocation in [('iot-llm-observer-rollout-29b','7c0137e4774a42fa9324f195ec908df6'),
                             ('iot-agent-feature-29m','31a740564de64890a7483cd2d81ba7a7'),('iot-agent-observation-29a','43de55832aff44bdae902f86f395697d')]:
        entries=[json.loads(v) for v in bodies['units/'+label+'/journal.stdout'].splitlines()]
        own=[v for v in entries if v.get('INVOCATION_ID')==invocation]
        assert any('Started ' in v.get('MESSAGE','') for v in own) and any('Deactivated successfully.' in v.get('MESSAGE','') for v in own)
        assert not any('Failed with result' in v.get('MESSAGE','') for v in own)
    failed=native('native/llm-observer-rollout-29a/verification.json')
    assert not failed['passed'] and failed['error']=='HTTP Error 400: Bad Request' and all(v['restored'] for v in failed['rollback'])
    assert native('capture-notes.json')['original_journals_preserved'] and not native('capture-notes.json')['business_workload_repeated']
    # The reused A launcher filename now records B. Journal identities, not that filename, establish the two invocations.
    assert b'7c0137e4774a42fa9324f195ec908df6' in bodies['launch/llm-observer-rollout-launch-29a.txt']
    accepted=native(rollout+'verification.json');assert accepted['passed'] and accepted['routing_changed'] and accepted['trace_environment']==TRACE_ENV
    assert accepted['source_commit']=='1bc089e62a0c37ac14cec16ac5742462d2375ccf' and accepted['tested_source_commit']=='97ba77b6d538b816b6304c17031090e3820a3fa0'
    cm=native(rollout+'envoy-config-map.json');assert cm['immutable'] and cm['metadata']['name']==accepted['new_immutable_config_map']=='iot-llm-router-observe-29b'
    config=json.loads(cm['data']['envoy.yaml']);listener=next(v for v in config['static_resources']['listeners'] if v['address']['socket_address']['port_value']==8081)
    manager=next(v['typed_config'] for v in listener['filter_chains'][0]['filters'] if v['name']=='envoy.filters.network.http_connection_manager')
    trace=manager['route_config']['virtual_hosts'][0]['routes'][0]
    assert trace['match']=={'path':TRACE_PATH,'headers':[{'name':':method','string_match':{'exact':'POST'}}]}
    assert trace['route']=={'cluster':TRACE_CLUSTER,'prefix_rewrite':'/v1/traces','timeout':'15s'}
    assert trace['typed_per_filter_config']['envoy.filters.http.ext_proc']['disabled']
    import copy
    old=copy.deepcopy(config);old['static_resources']['clusters'].pop()
    old_manager=next(v['typed_config'] for v in next(v for v in old['static_resources']['listeners'] if v['address']['socket_address']['port_value']==8081)['filter_chains'][0]['filters'] if v['name']=='envoy.filters.network.http_connection_manager')
    old_manager['route_config']['virtual_hosts'][0]['routes'].pop(0);assert private_trace_route(old)==config
    validator=native(rollout+'validation-pods.json')['items'];assert len(validator)==1
    assert validator[0]['status']['containerStatuses'][0]['state']['terminated']['exitCode']==0
    assert validator[0]['spec']['containers'][0]['image']==accepted['validated_envoy_image']=='docker.io/envoyproxy/envoy@sha256:85500e28ed088ec39ff0adc1be3d358a8ad062926aaa62c36b28bde00919e4e8'
    assert b'configuration \'/etc/envoy/envoy.yaml\' OK' in bodies[rollout+'validator-logs.stdout']
    before=native(rollout+'before-router.json');after=native(rollout+'after-router.json');planned=copy.deepcopy(before['spec'])
    next(v for v in planned['template']['spec']['volumes'] if v['name']=='config')['configMap']['name']=cm['metadata']['name']
    assert before['metadata']['uid']==after['metadata']['uid'] and after['spec']==planned
    before=native(rollout+'before-controller.json');after=native(rollout+'after-controller.json');planned=copy.deepcopy(before['spec'])
    planned['template']['spec']['containers'][0]['env']=controller_trace_environment(planned['template']['spec']['containers'][0]['env'])
    assert before['metadata']['uid']==after['metadata']['uid'] and after['spec']==planned
    before=native(rollout+'before-pool.json');after=native(rollout+'after-pool.json');planned=copy.deepcopy(before['spec'])
    planned['targetPorts']=[{'number':8081}];planned['selector']['matchLabels']={'app.kubernetes.io/instance':'iot-llm-observer','app.kubernetes.io/name':'iot-llm-observer'}
    assert before['metadata']['uid']==after['metadata']['uid'] and after['spec']==planned
    for name in ['control','gpu','model']:
        before=native(rollout+'before-'+name+'.json');after=native(rollout+'after-'+name+'.json')
        assert before['metadata']['uid']==after['metadata']['uid'] and before['spec']==after['spec']
    for name in ['before-agents','after-agents']:
        current={v['metadata']['uid']:v['spec'] for v in native(rollout+name+'.json')['items']}
        if name=='before-agents':business=current
        else:assert current==business
    assert native(rollout+'routed-generation-http.json')['status']==200 and native(rollout+'routed-generation-response.bin')['usage']['total_tokens']>0
    assert native(rollout+'routed-trace-empty-http.json')['status']==400 and native(rollout+'routed-trace-empty-response.bin')=={'error':'invalid_otlp_trace'}
    feature=native('native/agent-feature-29m/verification.json');assert feature['passed'] and feature['machines']==200 and feature['model_version']=='6'
    assert native('native/agent-feature-29m/physical-ttl-before.json')['keys']==0
    assert native('native/agent-feature-29m/physical-ttl-after.json')['keys']==200
    material=native('native/agent-feature-29m/materialization-container.json');assert material['image']==feature['sdk_image_id']
    assert material['state']['ExitCode']==0 and not material['state']['Running'] and not material['state']['OOMKilled']
    context=native('feature-state/context.json');ready=native('feature-state/_READY.json')
    assert ready['context_sha256']==sha256(bodies['feature-state/context.json']).hexdigest()
    assert ready['verification_sha256']==sha256(bodies['feature-state/verification.json']).hexdigest()
    assert context['prepared_wall_utc']==feature['prepared_wall_utc']=='2026-10-29T22:59:34.085035+00:00'
    report=native(chat+'verification.json');reply=native(chat+'coordinator-response.json')
    children={role:{kind:native(chat+role+'-'+kind+'-response.json') for kind in ['session','tasks']} for role in ['retrieval','drift']}
    actual=verify_coordinator(reply,children);assert actual==report['actual_acceptance'] and actual['passed']
    assert actual['coordinator_delegation_verified'] and actual['children']['retrieval']['successful_tool_events']==3 and actual['children']['drift']['successful_tool_events']==1
    assert report['all_chat_requests_through_https_ui_gateway'] and report['feature_seed_uid']==feature['seed_uid']
    assert datetime.fromisoformat(feature['prepared_wall_utc'])<datetime.fromisoformat(report['checked_utc'])<datetime.fromisoformat(feature['lease_expires_utc'])
    assert bodies[cohort+'conversation.exit']==b'0\n'
    before=native(cohort+'before-ledger.json');after=native(cohort+'after-ledger.json');observed=native(cohort+'observation.json')
    old={(v['kind'],v['event_id']):v for v in before};new={(v['kind'],v['event_id']):v for v in after};assert len(old)==len(before)==38 and len(new)==len(after)==818
    assert all(new[k]==v for k,v in old.items());fresh=[v for k,v in new.items() if k not in old]
    assert fresh==native(cohort+'new-ledger-rows.json') and len(fresh)==780
    requests=[v for v in fresh if v['kind']=='request'];spans=[v['facts'] for v in fresh if v['kind']=='span']
    assert len(requests)==observed['new_model_requests']==9 and len(spans)==observed['new_spans']==771
    assert all(v['facts']['result']=='ok' and v['facts']['status']==200 and v['facts']['streaming'] for v in requests)
    roots=[v for v in spans if v['operation']=='agent'];tools=[v for v in spans if v['operation']=='tool'];models=[v for v in spans if v['operation']=='model']
    assert Counter(v['agent'] for v in roots)=={'retrieval':1,'drift':1,'coordinator':1}
    assert len(models)==9 and len(tools)==6 and all(v['status_code']==0 and not v['error'] for v in roots+tools+models)
    traceids={v['trace_id'] for v in roots};assert traceids=={'caee7d7a23dae19af7e76c39a18d8b26'} and all(v['trace_id'] in traceids for v in tools+models)
    expected_tools={'get_machine_features','predict_failure','search_knowledge','assess_feature_drift','delegate_retrieval','delegate_drift'}
    assert Counter(v['tool'] for v in tools)=={v:1 for v in expected_tools}
    assert Counter(json.dumps(v['usage'],sort_keys=True) for v in models)==Counter(json.dumps(v['facts']['usage'],sort_keys=True) for v in requests)
    usage={k:sum(v['facts']['usage'][k] for v in requests) for k in ['prompt_tokens','completion_tokens','total_tokens']}
    assert usage=={'prompt_tokens':13549,'completion_tokens':1139,'total_tokens':14688}
    mcp={}
    for label in ['before','after']:
        pods=native(cohort+label+'-mcp-pods.json')['items'];live=[p for p in pods if not p['metadata'].get('deletionTimestamp') and p['status']['phase']=='Running' and all(c['ready'] for c in p['status']['containerStatuses'])]
        assert len(live)==4;totals=Counter();identity={}
        for pod in live:
            name=pod['metadata']['name'];identity[name]=(pod['metadata']['uid'],pod['spec'],[(c['name'],c['restartCount']) for c in pod['status']['containerStatuses']])
            assert native(cohort+label+'-mcp-'+name+'-http.json')['status']==200
            totals.update(samples(bodies[cohort+label+'-mcp-'+name+'.bin'],'iot_mcp_tool_calls_total'))
        if label=='before':identities=identity
        else:assert identities==identity
        mcp[label]=totals
    delta={k:mcp['after'][k]-mcp['before'][k] for k in set(mcp['before'])|set(mcp['after'])}
    changed={tuple(sorted(dict(k).items())):v for k,v in delta.items() if v}
    expected={tuple(sorted({'result':'success','role':role,'tool':tool}.items())):1 for role,tools in [('retrieval',['get_machine_features','predict_failure','search_knowledge']),('drift',['assess_feature_drift'])] for tool in tools}
    assert changed==expected and all(v>=0 for v in delta.values())
    for metric,field in [('input','prompt_tokens'),('output','completion_tokens'),('total','total_tokens')]:
        values={label:sum(v for k,v in samples(bodies[cohort+label+'-observer-metrics.bin'],'iot_llm_reported_tokens_total').items() if dict(k)['type']==metric) for label in ['before','after']}
        assert values['after']-values['before']==usage[field]
    stored={}
    for identity in observed['trace_ids']:
        name=cohort+'jaeger-trace-'+identity;assert native(name+'-http.json')['status']==200
        for line in bodies[name+'.bin'].splitlines():
            batch=json.loads(line)['result']
            for resource in batch['resourceSpans']:
                for scope in resource.get('scopeSpans',[]):
                    for span in scope.get('spans',[]):stored[(span['traceId'],span['spanId'])]=span
    for fact in spans:
        span=stored[(fact['trace_id'],fact['span_id'])]
        assert span.get('parentSpanId','')==fact['parent_span_id']
        assert int(span['startTimeUnixNano'])==fact['start_ns'] and int(span['endTimeUnixNano'])==fact['end_ns']
        assert span.get('status',{}).get('code',0)==fact['status_code']
        assert span['name']==fact['operation']+' '+(fact['tool'] if fact['operation']=='tool' else fact['model'] if fact['operation']=='model' else fact['agent'])
        assert not span.get('events') and not span.get('links') and not span.get('traceState')
    return {'passed':True,'native_payloads':2104,'actual_https_coordinator_conversations':1,'verified_child_sessions':2,
        'observed_agent_roots':3,'native_agent_status':'unset; answer/tool success checked separately','authoritative_mcp_call_delta':4,'mcp_failure_delta':0,
        'model_request_delta':9,'reported_usage':usage,'new_native_spans':771,'same_agent_trace_id':next(iter(traceids)),
        'scope':'Original active private routing, one complete HTTPS conversation, real feature lease and matched model/MCP/Jaeger facts. No dashboard, load, A/B, new per-request identity link between independent SDKs or full coursework completion claim.'}


if __name__=='__main__':print(json.dumps(verify(),indent=2))
