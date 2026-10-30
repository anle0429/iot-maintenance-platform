"""Verify the sealed restart recovery and paired native agent configurations, including failed predecessor tasks."""
from datetime import datetime,timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import importlib.util,json,sys,tarfile
from integration_evidence import ROOT,capsule,read
sys.path.insert(0,str(ROOT))
from agents.ab_configuration import VARIANTS,comparison_summary,configuration_plan
from agents.answer_acceptance import verify_specialist,native_tools
from agents.probe import QUESTIONS

MANIFEST='8f9675717d11e6836a5c430d0a32a677a6d01566a95ab87902afc48db35f39cb'


def unpack(raw):
    """Authenticate original archive members without extracting paths or accepting archive links."""
    bodies={}
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            assert member.isdir() or member.isfile()
            if not member.isfile():continue
            name=PurePosixPath(member.name).relative_to('llm-agent-ab-capture-30a')
            assert not name.is_absolute() and '..' not in name.parts and member.size<10000000 and name.as_posix() not in bodies
            bodies[name.as_posix()]=archive.extractfile(member).read()
    assert sha256(bodies['manifest.json']).hexdigest()=='5cedf576a85c71ac59fc370bcd44ab67c018b80e56257a95d00562991b6facc2'
    inventory=json.loads(bodies['manifest.json']);assert len(inventory['files'])==1044 and set(bodies)==set(inventory['files'])|{'manifest.json'}
    for name,pin in inventory['files'].items():assert len(bodies[name])==pin['bytes'] and sha256(bodies[name]).hexdigest()==pin['sha256'],name
    return bodies


def verify():
    """Recompute all accepted answers, native prediction arithmetic, configuration boundaries and recovery invariants."""
    base,count=capsule('llm-agent-ab',MANIFEST);assert count==19
    metadata=read(base,'native-archive.json');raw=(base/'native-receipts.tar.gz').read_bytes()
    assert len(raw)==metadata['archive_bytes']==463261 and sha256(raw).hexdigest()==metadata['archive_sha256']=='9e24257746c9a377229f58355c0ef60e47e6cd002306ee4a693b2c3e3f4d1dbe'
    assert metadata['current_native_credentials_scanned'] and metadata['pc_actual_gpu_join_credential_scanned']
    assert not metadata['recompressed_or_rewritten_on_pc'] and not metadata['workloads_rerun_for_capture']
    bodies=unpack(raw);native=lambda name:json.loads(bodies[name]);out=lambda name:'native/'+name+'/'
    current={'demo-resume-5b234e25','substrate-members-224ef519','agent-ab-7c59f103','cpu-standby-f10af11a','worker-address-4e6ed69f','agent-study-2015d649','agent-ab-view-2015d649'}
    folders={n.split('/')[1] for n in bodies if n.startswith('source/')};assert len(folders)==9
    for folder in folders:
        prefix='source/'+folder+'/';seal=native(prefix+'manifest.json')
        for name,pin in seal['files'].items():
            original=bodies[prefix+'tree/'+name];assert sha256(original).hexdigest()==pin
            if folder in current:
                working=(ROOT/name).read_bytes()
                assert working==original or working.replace(b'\r\n',b'\n')==original,name
    config=out('agent-ab-deploy-30b');accepted=native(config+'verification.json');assert accepted['passed'] and accepted['resumed_existing_variants']
    policy=native('source/agent-ab-7c59f103/tree/agents/response-policy.json')
    plan=configuration_plan(native(config+'model-before.json'),native(config+'retrieval-before.json'),policy)
    assert plan==native(config+'planned.json')==native(out('agent-ab-deploy-30a')+'planned.json')
    assert native(config+'resume-origin.json')['same_four_uids_verified']
    templates=native(config+'templates.json')['items'];projection=native(config+'compiled-projections.json')
    for variant,settings in VARIANTS.items():
        model=native(config+'model-'+variant+'.json');entry=projection[variant]
        assert model['metadata']['uid']==entry['model_config_uid'] and model['spec']==next(v['spec'] for v in plan if v['metadata']['name']==settings['model_config'])
        assert model['status']['observedGeneration']==model['metadata']['generation']
        assert entry['temperature_fields']==[{'path':'/model/temperature','value':float(settings['temperature'])}]
        assert not entry['full_secret_archived']
        template=next(t for t in templates if t['metadata']['uid']==entry['template_uid'])
        assert template['status']['phase']=='Ready' and template['status']['goldenSnapshot'] and template['spec']['sandboxClass']=='gvisor'
        assert any(o['uid']==entry['agent_uid'] for o in template['metadata']['ownerReferences'])
        env={v['name']:v for v in template['spec']['containers'][0]['env']}
        assert env['KAGENT_CONFIG_JSON']['valueFrom']['secretKeyRef']=={'name':settings['agent'],'key':'config.json'}
    feature=native(out('feature-refresh-30b')+'verification.json');assert feature['passed'] and feature['resume_deploy'] and feature['machines']==200
    prepared=datetime.fromisoformat(feature['prepared_wall_utc']);expiry=datetime.fromisoformat(feature['lease_expires_utc']);assert expiry-prepared==timedelta(hours=1)
    assert native(out('feature-refresh-30a')+'physical-ttl-before.json')=={'keys':0,'pttl_ms':[]}
    ttl=native(out('feature-refresh-30a')+'physical-ttl-after.json');assert ttl['keys']==200 and len(ttl['pttl_ms'])==200 and min(ttl['pttl_ms'])>600000
    assert sha256(bodies['helpers/iot-refresh-demo-feature-29k.py']).hexdigest()==feature['source_sha256']=='ec2be13e02a218a5e583c7006ce418648ee8bb1829acb1c5d9b10f89e41552c0'
    spec=importlib.util.spec_from_file_location('independent_prediction_check',ROOT/'scripts/demo-feature-api.py');arithmetic=importlib.util.module_from_spec(spec);spec.loader.exec_module(arithmetic)
    pin=native('models/model-pin.json');portable=native('models/portable.json')
    assert pin==read(ROOT/'docs/evidence/ci-serving-linux/selected-model6','model-pin.json') and pin['version']=='6'
    assert sha256(bodies['models/portable.json']).hexdigest()==pin['portable_sha256']=='60dff76d4d27a9e248bc08a80c4da80a3212701774c00f89f0b16139e1609981'
    counts={'successful_tool_events':0,'tool_error_events':0};max_error=0.;all_contexts=set()
    for suffix in ['a','b']:
        prefix=out('agent-ab-study-30'+suffix);rows=native(prefix+'outcomes.json');machines=native(prefix+'machines.json');summary=comparison_summary(rows,machines)
        assert machines==['P-001','P-002','P-003'] and summary==native(prefix+'summary.json')==native(prefix+'verification.json')['summary']
        assert [(v['variant'],v['machine_id']) for v in rows]==[('a','P-001'),('b','P-001'),('b','P-002'),('a','P-002'),('a','P-003'),('b','P-003')]
        assert len({v['request_id'] for v in rows})==len({v['context_id'] for v in rows})==6
        for row in rows:
            case=machines.index(row['machine_id']);label=str(case)+'-'+row['variant']+'/'
            request=native(prefix+label+'request.json');reply=native(prefix+label+'response.json');task=reply['result']
            assert request['id']==row['request_id'] and request['params']['message']['contextId']==task['contextId']==row['context_id']
            assert row['context_id'] not in all_contexts;all_contexts.add(row['context_id'])
            assert request['params']['message']['parts']==[{'kind':'text','text':QUESTIONS['retrieval'].replace('P-001',row['machine_id'])}]
            assert row['http_status']==200 and row['response_complete']
            assert native(prefix+label+'outcome.json')==row
            if suffix=='a':
                assert not row['passed'] and task['status']['state']=='failed' and not task.get('artifacts')
                assert 'connect: connection refused' in task['status']['message']['parts'][0]['text']
            else:
                proof=verify_specialist(reply,'retrieval',machine_id=row['machine_id'],knowledge_root=ROOT/'knowledge')
                assert proof==native(prefix+label+'answer-acceptance.json') and row['passed']
                tools,actual_counts=native_tools(task,'retrieval');assert actual_counts=={'successful_tool_events':3,'tool_error_events':0}
                for key,value in actual_counts.items():counts[key]+=value
                stamp=datetime.fromisoformat(task['status']['timestamp'].replace('Z','+00:00'));assert prepared<=stamp<expiry
                trace=tools['get_machine_features']['output'];prediction=tools['predict_failure']['output']
                expected=arithmetic.check_prediction(trace,prediction,portable,pin,stamp)
                max_error=max(max_error,abs(expected-prediction['prediction']['risk_score']))
        if suffix=='a':assert all(v['tool_grounded_answers']==0 for v in summary.values())
        else:
            assert all(v['requests']==v['tool_grounded_answers']==3 for v in summary.values())
            assert native(prefix+'production-agents-before.json')['items'] and native(prefix+'shared-model-before.json')['spec']==native(prefix+'shared-model-after.json')['spec']
            for role in ['retrieval','drift']:
                ready=native(prefix+role+'-mcp-endpoints.json');assert sum(e.get('conditions',{}).get('ready') is True for v in ready['items'] for e in v['endpoints'])==2
    assert len(all_contexts)==12 and counts=={'successful_tool_events':18,'tool_error_events':0}
    recovery=native(out('retrieval-mcp-recovery-30a')+'verification.json');assert recovery['passed'] and recovery['before_ready']==0 and recovery['after_ready']==2
    before=native(out('retrieval-mcp-recovery-30a')+'deployment-before.json');after=native(out('retrieval-mcp-recovery-30a')+'deployment-after.json')
    assert before['metadata']['uid']==after['metadata']['uid'] and before['spec']['template']['spec']==after['spec']['template']['spec']
    members=native(out('substrate-members-30b')+'verification.json');assert members['passed'] and not members['address_repair_applied'] and members['membership_commands']==0
    assert members['node_ids']==native(out('substrate-members-30a')+'before.json')['node_ids'] and len(set(members['node_ids'].values()))==6
    for value in members['cluster_info'].values():assert value['cluster_state']=='ok' and int(value['cluster_slots_ok'])==16384 and int(value['cluster_known_nodes'])==6
    assert not native(out('substrate-members-30a')+'verification.json')['passed']
    workers=native(out('worker-address-recovery-30a')+'verification.json');assert workers['passed'] and workers['pods_restarted'] and not workers['actors_reset'] and workers['after']==workers['expected_after']
    unpause=native(out('worker-recovery-unpause-30b')+'verification.json');assert unpause['passed'] and unpause['active_hpas']==3 and unpause['temporary_pause_annotations_remaining']==0
    assert set(unpause['native_paused_conditions'].values())=={'False'} and not unpause['status_was_manually_edited']
    parked=native(out('cpu-model-standby-30a')+'verification.json');assert parked['passed'] and parked['legacy_cpu_replicas_before']==1 and parked['legacy_cpu_replicas_after']==0 and parked['gpu_models_loaded']==2
    assert parked['cpu_container_requests_preserved']=={'cpu':'1500m','memory':'4Gi'}
    resumed=native(out('demo-resume-30b')+'verification.json');assert resumed['passed'] and all(v==200 for v in resumed['health_http'].values())
    assert datetime.fromisoformat(resumed['session']['auto_stop_utc'])-datetime.fromisoformat(resumed['session']['last_start_utc'])==timedelta(hours=8)
    assert set(resumed['steps']['scoped_token']['identities'])=={'iot-api-metrics','iot-llm-metrics-reader'} and resumed['steps']['scoped_token']['permissions_preserved']
    view=out('agent-ab-view-30a');panels=native(view+'panels.json');assert len(panels)==3
    for panel in panels:
        def vector(kind):
            """Compare original scalar values by variant labels across independent Prometheus/Grafana reads."""
            value=native(view+'panel-'+str(panel['id'])+'-'+kind+'.json');assert value['status']=='success'
            return {v['metric']['variant']:float(v['value'][1]) for v in value['data']['result']}
        assert vector('prometheus')==vector('grafana')=={k:float(v) for k,v in panel['values'].items()}
    assert native(view+'verification.json')['passed'] and native(view+'historical-model-metrics-restored.json')['new_model_requests']==0
    units=[('iot-demo-resume-30b','a51fabe3ca814acdabfd3cf011c421c5'),('iot-agent-ab-deploy-30b','ec3f6368ead046199f2edb4123ca140f'),
        ('iot-agent-ab-study-30b','07bff0fc899f47339a2c262d6a72cc0a'),('iot-agent-ab-view-30a','e2ab0299ea2b411eba0100e23b0de2cf')]
    for unit,identity in units:
        lines=[json.loads(v) for v in bodies['units/'+unit+'/journal.stdout'].splitlines()];own=[v for v in lines if v.get('INVOCATION_ID')==identity]
        assert any('Started ' in v.get('MESSAGE','') for v in own) and any('Deactivated successfully.' in v.get('MESSAGE','') for v in own)
    for photo in read(base,'ui/index.json')['files']:
        body=(base/'ui'/photo['file']).read_bytes();assert body.startswith(b'\xff\xd8\xff') and len(body)==photo['bytes'] and sha256(body).hexdigest()==photo['sha256']
    print(json.dumps({'passed':True,'native_payloads':1044,'paired_completed_answers':6,'native_successful_mcp_events':18,'failed_predecessor_tasks_retained':6,
        'independent_prediction_max_error':max_error,'numeric_dashboard_matches':3,'temporary_pause_annotations_remaining':0,'scope':'Recorded recovery and narrow paired agent configurations; no general quality ranking or whole-coursework completion claim.'},indent=2))


if __name__=='__main__':verify()
