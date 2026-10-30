"""Verify original OPA capability/budget decisions and the actual denied and deployed production agent pipelines without starting services."""
from collections import Counter
from datetime import datetime,timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import importlib.util,json,sys,tarfile,xml.etree.ElementTree as ET
from integration_evidence import ROOT,capsule,read
sys.path.insert(0,str(ROOT))
from agents.admission import canonical,decision_value,require_same_admitted_input
from agents.admission_cases import cases
from agents.admission_ci import require_policy_phase,production_agents
from agents.ci_contract import release_identity,require_receipt
from agents.release_ci import api_quality,ready_agents,feature_lease,unchanged_quality_source
from agents.answer_acceptance import verify_specialist,native_tools
from agents.delegation_acceptance import verify_coordinator
MANIFEST='013946a5905c2b5b0c3e5e9ebb4781482cef71d867f877797398b646ac8ad964'
COMMIT='beb7f5c7dbec14e8429a0877c87eb17e6c901b7b'


def unpack(raw,prefix):
    """Read safe regular archive members in memory, avoiding Windows path-length limits and all filesystem extraction."""
    files={}
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            assert member.isdir() or member.isfile()
            if not member.isfile():continue
            name=PurePosixPath(member.name).relative_to(prefix)
            assert not name.is_absolute() and '..' not in name.parts and member.size<10000000 and name.as_posix() not in files
            files[name.as_posix()]=archive.extractfile(member).read()
    return files


def generated(raw):
    """Read the independent native GPU output-token counter rather than inferring tokens from text length."""
    values=[float(v.split()[-1]) for v in raw.decode().splitlines() if v.startswith('llamacpp:tokens_predicted_total ')]
    assert len(values)==1;return values[0]


def verify():
    """Authenticate all sources/receipts, recompute native answer acceptance and verify the exact policy/deployment boundary."""
    base,count=capsule('agent-admission',MANIFEST);assert count==18
    metadata=read(base,'native-archive.json');raw=(base/'native-receipts.tar.gz').read_bytes()
    assert len(raw)==metadata['archive_bytes']==1849289 and sha256(raw).hexdigest()==metadata['archive_sha256']=='b3ccb40c0eada0b100a1cc2a6dca53f3aed5708d1c5c09475b81a8f1ba842a0b'
    assert metadata['current_native_credentials_scanned'] and metadata['supplemental_vault_root_unseal_scan_passed'] and metadata['pc_actual_gpu_join_credential_scanned']
    assert not metadata['recompressed_or_rewritten_on_pc'] and not metadata['workloads_rerun_for_capture']
    bodies=unpack(raw,'agent-admission-capture-30a');assert sha256(bodies['manifest.json']).hexdigest()=='e316d716e2bea127c327f05b965a1150fa2f0ab6b3b430e53e5485751d98e917'
    inventory=json.loads(bodies['manifest.json']);assert len(inventory['files'])==1833 and set(bodies)==set(inventory['files'])|{'manifest.json'}
    for name,pin in inventory['files'].items():assert len(bodies[name])==pin['bytes'] and sha256(bodies[name]).hexdigest()==pin['sha256'],name
    native=lambda name:json.loads(bodies[name])
    supplement=(base/'vault-scan.tar.gz').read_bytes();assert len(supplement)==1592 and sha256(supplement).hexdigest()==metadata['vault_scan_archive_sha256']=='6fbb4148f440081dea5351d5053bb8accffcfcd743bc069de6c132082ab4428a'
    scan=unpack(supplement,'agent-admission-vault-scan-30a');assert set(scan)=={'source.py','verification.json'}
    scanned=json.loads(scan['verification.json']);assert scanned==read(base,'vault-scan.json') and scanned['passed']
    assert scanned['original_archive_sha256']==metadata['archive_sha256'] and scanned['native_payloads_scanned']==1833 and scanned['current_vault_root_and_unseal_values_scanned']>=2 and not scanned['native_execution_files_changed']
    folders={'agent-admission-b8251dec':9,'agent-admission-727cb6db':9,'ci-policy-388632450333af8c8f7cc925486c88f9c101c233':375,'ci-policy-'+COMMIT:375}
    for folder,size in folders.items():
        prefix='source/'+folder+'/';seal=native(prefix+'manifest.json');assert len(seal['files'])==size
        for name,pin in seal['files'].items():
            original=bodies[prefix+'tree/'+name];assert sha256(original).hexdigest()==pin
            if folder in {'agent-admission-727cb6db','ci-policy-'+COMMIT}:
                current=(ROOT/name).read_bytes();assert current==original or current.replace(b'\r\n',b'\n')==original,name
    declared=cases(json.loads((ROOT/'tests/fixtures/agent-admission-observed.json').read_bytes()));assert len(declared)==70 and sum(v['expected_allowed'] for v in declared)==4
    suite='native/agent-admission-contracts-30b/';proof=native(suite+'verification.json');assert proof==read(base,'policy-contracts-verification.json') and proof['passed']
    assert proof['allowed_cases']==4 and proof['denied_cases']==66 and proof['post_admission_tool_edit_rejected'] and proof['deployment_commands_run']==proof['model_requests']==0
    for case,row in zip(declared,proof['cases'],strict=True):
        prefix=suite+case['id']+'/';value=native(prefix+'input.json');assert value==case['input'] and row['case']==case['id']
        decision=decision_value(native(prefix+'decision.stdout'));assert decision['allowed']==row['allowed']==case['expected_allowed'] and decision['violations']==row['violations']
        assert native(prefix+'gate-exit.json')['exit_code']==row['gate_exit']==(0 if case['expected_allowed'] else 1)
        assert row['input_sha256']==sha256(canonical(value)).hexdigest()
        gate=native(prefix+'gate.stdout')
        if case['expected_allowed']:require_same_admitted_input(row,value);assert gate['result'][0]['expressions'][0]['value'] is True
        else:assert not gate.get('result')
    predecessor='native/agent-admission-contracts-30a/wrong-envelope-version/'
    assert decision_value(native(predecessor+'decision.stdout'))['allowed'] is False
    assert native(predecessor+'gate-exit.json')['exit_code']==0 and native(predecessor+'gate.stdout')['result'][0]['expressions'][0]['value'] is False
    assert 'native/agent-admission-contracts-30a/verification.json' not in bodies
    assert native('native/agent-policy-ci-stage-30b/verification.json')['only_pipeline_and_declared_parameters_changed']
    source=native('source/ci-policy-'+COMMIT+'/manifest.json');source_digest=sha256(json.dumps(source,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    feature=native('native/feature-policy-30a/verification.json');assert feature==read(base,'fresh-feature-verification.json') and feature['passed'] and feature['machines']==200 and feature['model_version']=='6' and not feature['resume_deploy']
    prepared=datetime.fromisoformat(feature['prepared_wall_utc']);expiry=datetime.fromisoformat(feature['lease_expires_utc']);assert expiry-prepared==timedelta(hours=1)
    assert native('native/feature-policy-30a/physical-ttl-before.json')=={'keys':0,'pttl_ms':[]}
    ttl=native('native/feature-policy-30a/physical-ttl-after.json');assert ttl['keys']==200 and len(ttl['pttl_ms'])==200 and min(ttl['pttl_ms'])>600000
    assert sha256(bodies['helpers/iot-refresh-demo-feature-29k.py']).hexdigest()==feature['source_sha256']=='ec2be13e02a218a5e583c7006ce418648ee8bb1829acb1c5d9b10f89e41552c0'
    negative='ci/llm-coordinator-jenkins-4/';denied=native('native/agent-policy-ci-finish-negative-30a/verification.json');assert denied==read(base,'negative-ci-verification.json') and denied['passed']
    assert denied['jenkins_result']=='FAILURE' and not denied['policy_allowed'] and denied['policy_gate_exit']==1 and denied['native_generated_token_delta']==0
    decision=native(negative+'policy-decision/verification.json');assert not decision['allowed'] and decision['violations']==['unapproved capability or missing required field: SandboxAgent/iot-retrieval']
    for name in ['policy.json','deployment-plan.json','deploy.json','verify.json','admitted-deploy-host.attempt']:assert negative+name not in bodies
    console=bodies['native/agent-policy-ci-finish-negative-30a/console.txt'];assert b'Stage "Auto deploy MCP and accepted agent policy" skipped due to earlier failure(s)' in console
    assert b'OPA denied this candidate before any deployment command' in bodies[negative+'policy.txt']
    before=native('native/agent-policy-ci-trigger-negative-30a/resources-before.json');after=native('native/agent-policy-ci-finish-negative-30a/resources-after.json')
    def identities(value):
        """Compare resource UIDs and entire specs, excluding automatic status/resource-version updates."""
        return {v['metadata']['name']:{'uid':v['metadata']['uid'],'spec':v['spec']} for v in value['items']}
    assert all(identities(before[name])==identities(after[name]) for name in before)
    assert native('native/agent-policy-ci-trigger-negative-30a/helm-before.json')==native('native/agent-policy-ci-finish-negative-30a/helm-after.json')
    assert generated(bodies['native/agent-policy-ci-trigger-negative-30a/model-before.prom'])==generated(bodies['native/agent-policy-ci-finish-negative-30a/model-after.prom'])
    negative_input=native(negative+'policy-decision/input.json');baseline=declared[0]['input'];expected=next(v['input'] for v in declared if v['id']=='unsafe-hardware-tool');assert negative_input==expected
    policy=json.loads((ROOT/'agents/response-policy.json').read_bytes());counts=Counter();deltas={};windows=[];specialists=[]
    for role in ['coordinator','retrieval','drift']:
        prefix='ci/llm-'+role+'-jenkins-5/';finish='native/agent-policy-ci-finish-production-'+role+'-30a/';trigger='native/agent-policy-ci-trigger-production-'+role+'-30a/'
        job=native(finish+'build.json');assert job['number']==5 and not job['building'] and job['result']=='SUCCESS'
        params={p['name']:p['value'] for a in job['actions'] for p in a.get('parameters',[])};assert params=={'SOURCE_COMMIT':COMMIT,'ADMISSION_SCENARIO':'production'}
        stages=native(finish+'stages.json');assert stages['status']=='SUCCESS' and len(stages['stages'])==8 and all(v['status']=='SUCCESS' for v in stages['stages'])
        windows.append((job['timestamp'],job['timestamp']+job['duration']));identity=release_identity(role,5,COMMIT);phases={}
        for phase in ['test','build','deploy','verify']:phases[phase]=require_receipt(native(prefix+phase+'.json'),identity,phase)
        receipt=require_policy_phase(native(prefix+'policy.json'),identity,source_digest);actual=native(prefix+'policy-decision/input.json');assert actual==baseline
        require_same_admitted_input(receipt['decision'],actual);recheck=native(prefix+'policy-recheck-decision/verification.json')
        require_same_admitted_input(recheck,native(prefix+'policy-recheck-decision/input.json'));assert recheck['input_sha256']==receipt['decision']['input_sha256']
        assert sha256(bodies[prefix+'policy-candidate-rendered.yaml']).hexdigest()==sha256(bodies[prefix+'policy-recheck-rendered.yaml']).hexdigest()==receipt['rendered_manifest_sha256']
        for attempt in ['policy-decision','policy-recheck-decision']:
            assert decision_value(native(prefix+attempt+'/decision.stdout'))=={'allowed':True,'violations':[]} and native(prefix+attempt+'/gate-exit.json')['exit_code']==0
        for phase in ['test','build','policy','admitted-deploy','verify']:
            container=native(prefix+phase+'-container.json');state=container['state']
            assert container['commit']==COMMIT and not state['Running'] and not state['OOMKilled'] and state['ExitCode']==0
        live=native(prefix+'before-deploy.json');assert ready_agents(production_agents(live['agents']['items']),policy)
        assert live['feature_seed_uid']==feature['seed_uid'] and feature_lease(feature,datetime.fromisoformat(live['feature_lease']['checked_utc']))==live['feature_lease']
        assert identities(native(prefix+'all-models-before-deploy.json'))==identities(native(prefix+'all-models-after-deploy.json'))
        for kind in ['agents','models']:
            left={k:v for k,v in identities(native(prefix+'all-'+kind+'-before-deploy.json')).items() if k.startswith('iot-ab-')}
            right={k:v for k,v in identities(native(prefix+'all-'+kind+'-after-deploy.json')).items() if k.startswith('iot-ab-')}
            assert len(left)==2 and left==right
        assert native(prefix+'retained-experiments.json')['passed']
        response=native(prefix+'agent-request/'+role+'-response.json');transport=native(prefix+'agent-request/'+role+'-transport.json');assert transport['http_status']==200 and transport['response_complete']
        if role=='coordinator':
            children={r:{k:native(prefix+'agent-request/'+r+'-'+k+'.json') for k in ['session','tasks']} for r in ['retrieval','drift']}
            checked=verify_coordinator(response,children,knowledge_root=ROOT/'knowledge')
            for child_role,child in children.items():
                selected=[v for v in child['tasks']['data'] if v['contextId']==checked['children'][child_role]['context_id']];assert len(selected)==1;specialists.append((child_role,selected[0]))
        else:
            checked=verify_specialist(response,role,knowledge_root=ROOT/'knowledge');specialists.append((role,response['result']))
            quality=api_quality(native(prefix+'api-tests/coverage.json'),ET.fromstring(bodies[prefix+'api-tests/junit.xml']));assert quality==phases['test']['fresh_api']
            cached=phases['test']['contracts']['unchanged_mcp_quality_cache'];assert cached['source_bindings']==unchanged_quality_source(ROOT) and not cached['fresh_mutation_run'] and cached['counts']['killed']==681 and sum(cached['counts'].values())==850
        assert checked==native(prefix+'agent-acceptance.json')==phases['verify']['actual_agent_acceptance']
        deltas[role]=generated(bodies[finish+'model-after.prom'])-generated(bodies[trigger+'model-before.prom']);assert deltas[role]==native(finish+'verification.json')['native_generated_token_delta']>0
    assert all(windows[i][1]<=windows[i+1][0] for i in range(2)) and deltas=={'coordinator':1139.,'retrieval':203.,'drift':494.}
    spec=importlib.util.spec_from_file_location('admission_prediction_oracle',ROOT/'scripts/demo-feature-api.py');oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
    pin=native('models/model-pin.json');portable=native('models/portable.json');assert pin['version']=='6' and sha256(bodies['models/portable.json']).hexdigest()==pin['portable_sha256']=='60dff76d4d27a9e248bc08a80c4da80a3212701774c00f89f0b16139e1609981'
    max_error=0.
    for role,task in specialists:
        tools,totals=native_tools(task,role);counts.update(totals);stamp=datetime.fromisoformat(task['status']['timestamp'].replace('Z','+00:00'));assert prepared<=stamp<expiry
        if role=='retrieval':
            result=tools['predict_failure']['output'];expected=oracle.check_prediction(tools['get_machine_features']['output'],result,portable,pin,stamp)
            max_error=max(max_error,abs(expected-result['prediction']['risk_score']))
    assert len(specialists)==4 and counts=={'successful_tool_events':8,'tool_error_events':0} and max_error==0
    assert native('native/agent-policy-production-30a/verification.json')==read(base,'production-ci-verification.json')
    for unit,identity,passed in [('iot-agent-admission-contracts-30a','29b9903975174d1e9e44382c8055592b',False),('iot-agent-admission-contracts-30b','7ffe40395c0d40ea966043c59bbb33a8',True),('iot-feature-policy-30a','3ed47d35895f43ff9b4fbde446701bde',True),('iot-agent-policy-production-30a','3a5ded284f9c41d89f158533f458f1e1',True)]:
        own=[json.loads(v) for v in bodies['units/'+unit+'/journal.stdout'].splitlines() if json.loads(v).get('INVOCATION_ID')==identity]
        assert any('Started ' in v.get('MESSAGE','') for v in own)
        assert any(('Deactivated successfully.' if passed else "Failed with result 'exit-code'") in v.get('MESSAGE','') for v in own)
    for photo in read(base,'ui/index.json')['files']:
        body=(base/'ui'/photo['file']).read_bytes();assert body.startswith(b'\xff\xd8\xff') and len(body)==photo['bytes'] and sha256(body).hexdigest()==photo['sha256']
    for role in ['coordinator','retrieval','drift']:
        text=(base/'ui'/f'{role}.ax.txt').read_text();assert 'Admit model tools and sandbox with OPA' in text and '#5' in text
    assert 'skipped due to earlier failure(s)' in (base/'ui/negative-console.ax.txt').read_text()
    print(json.dumps({'passed':True,'native_payloads':1833,'allowed_policy_cases':4,'denied_policy_cases':66,'intentional_denied_build':'coordinator4',
        'actual_successful_production_builds':3,'successful_specialist_tool_events':8,'native_gpu_output_tokens':deltas,'independent_prediction_max_error':max_error,
        'scope':'Source-bound capability/budget admission for the owned production agent chart. No global admission-webhook or universal security claim.'},indent=2))


if __name__=='__main__':verify()
