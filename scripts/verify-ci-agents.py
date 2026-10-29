"""Verify separate native MCP/agent CI releases, original failures and complete delegated answers without starting services."""
from datetime import datetime,timedelta,timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json,re,subprocess,sys,tarfile,xml.etree.ElementTree as ET
from integration_evidence import ROOT,capsule,read
sys.path.insert(0,str(ROOT))
from agents.ci_contract import release_identity,require_receipt,validate_source
from agents.release_ci import MCP_RUNTIME,MCP_TESTS,api_quality,feature_lease,packaged_paths,ready_agents,required_inputs,unchanged_quality_source
from agents.answer_acceptance import verify_specialist
from agents.delegation_acceptance import verify_coordinator
from ml.serving_ci import check_pods

MANIFEST_SHA256='fba80fefc43f70657b27c0dd8abb5e1d5fe2994cab17bc60aff800478f5f92da'
COMMIT='daee5d3ff335917ffb7f1705e17bf1e723aa20ad'
ACCEPTED={'retrieval':4,'drift':4,'coordinator':3}
FIRST='aa7a0d9d37c2c507b5e13960e06720154b522850'
SECOND='3f07ca79e146780a8b40856a85937ba44448ead0'


def scalar_chart(raw):
    """Read the six scalar fields used by these exact chart metadata files without importing a YAML runtime."""
    result={}
    for line in raw.decode().splitlines():
        if not line.strip() or line.startswith('#'):continue
        if line.startswith('  '):
            assert key=='description' and ':' not in line
            result[key]+=' '+line.strip();continue
        key,value=line.split(':',1);assert re.fullmatch('[A-Za-z]+',key) and key not in result
        result[key]=value.strip().strip("'\"")
    assert set(result)=={'apiVersion','name','description','type','version','appVersion'}
    return result


def tool_probe(value,role,release):
    """Recheck original SDK business results, replay and rejected requests independently of its success flag."""
    assert value['passed'] and value['role']==role and value['run']==release and value['wrong_token_status']==401
    assert value['initialized']['serverInfo']['name']=='iot-'+role
    calls=value['calls'];expected=['get_machine_features','predict_failure','search_knowledge'] if role=='retrieval' else ['assess_feature_drift']
    assert {t['name'] for t in value['tools']['tools']}==set(expected)
    def answer(index,error=False):
        """Decode an original SDK tool result only after checking its expected success or rejection state."""
        response=calls[index]['response'];assert response['isError'] is error
        if not error:return json.loads(response['content'][0]['text'])
    if role=='retrieval':
        assert len(calls)==9 and value['native_snapshot_rows']==18
        first=answer(0);assert first==answer(1) and first['machine_id']=='P-001'
        prediction=answer(2);assert prediction['feature_trace']==first and prediction['model']['version']=='6'
        for index in [3,4,5,8]:answer(index,True)
        found=answer(6);assert found==answer(7) and len(found['matches'])==3
        assert 'monitoring' in [r['document_id'] for r in found['matches']]
        for row in found['matches']:
            source=(ROOT/'knowledge'/row['source_file']).read_bytes()
            assert sha256(source).hexdigest()==row['source_sha256'] and row['text'] in source.decode()
    else:
        assert len(calls)==3 and len(set(calls[0]['arguments']['machine_ids']))==32
        assert calls[1]['arguments']['machine_ids']==list(reversed(calls[0]['arguments']['machine_ids']))
        first=answer(0);assert first==answer(1) and first['unique_rows']==32
        assert first['reference_id']==value['assessment_reference_id'] and first['assessment_sha256']==value['assessment_sha256']
        answer(2,True)


def verify():
    """Authenticate raw artifacts and reconcile actual tests, source, images, deployment clocks and role histories."""
    base,count=capsule('ci-agents',MANIFEST_SHA256);provenance=read(base,'native-archive.json')
    raw=(base/'native-receipts.tar.gz').read_bytes()
    assert len(raw)==provenance['archive_bytes']==2421457
    assert sha256(raw).hexdigest()==provenance['archive_sha256']=='38769bd8b0fd5de668dbed09d61d15d162f97c06da3a8768bcfce4f25d168994'
    assert provenance['archive_repacked'] is False and provenance['coursework_complete'] is False
    bodies={}
    with tarfile.open(fileobj=BytesIO(raw),mode='r:gz') as packet:
        for member in packet:
            assert member.isfile() or member.isdir()
            if not member.isfile():continue
            name=Path(member.name).relative_to('agent-ci-capture-29a').as_posix()
            assert '..' not in Path(name).parts and name not in bodies
            bodies[name]=packet.extractfile(member).read()
    inventory=json.loads(bodies['manifest.json'])
    assert sha256(bodies['manifest.json']).hexdigest()==provenance['original_inventory_sha256']=='838324846ef398b92014a83cbb178298a4ee3969a6ab699b2ab58128b8388ae8'
    assert len(inventory['files'])==provenance['native_payloads']==1605 and set(bodies)==set(inventory['files'])|{'manifest.json'}
    for name,meta in inventory['files'].items():assert len(bodies[name])==meta['bytes'] and sha256(bodies[name]).hexdigest()==meta['sha256'],name
    def original(name):
        """Read one hash-authenticated original JSON response."""
        return json.loads(bodies[name])
    capture=original('capture.json')
    assert capture['passed'] and capture['accepted_builds']==ACCEPTED and capture['source_commit']==COMMIT
    assert capture['actual_secret_values_scanned']==10 and capture['credential_values_archived'] is False
    assert set(capture['source_revisions'])=={FIRST,SECOND,COMMIT}
    for commit,size in [(FIRST,360),(SECOND,362),(COMMIT,362)]:
        seal=original('source/'+commit+'/manifest.json');assert seal['commit']==commit and len(seal['files'])==size
        for name,digest in seal['files'].items():assert sha256(bodies['source/'+commit+'/tree/'+name]).hexdigest()==digest
    seal=original('source/'+COMMIT+'/manifest.json');source_digest=validate_source(ROOT,seal,COMMIT);required_inputs(ROOT,seal)
    for name in seal['files']:assert bodies['source/'+COMMIT+'/tree/'+name]==(ROOT/name).read_bytes(),name
    policy=json.loads((ROOT/'agents/response-policy.json').read_bytes());cache_bindings=unchanged_quality_source(ROOT)
    feature=original('features/agent-feature-29k/verification.json')
    assert feature['passed'] and feature['machines']==200 and feature['model_version']=='6' and feature['ready']['ready']
    assert feature['source_sha256']==sha256((ROOT/'scripts/refresh-demo-feature.py').read_bytes()).hexdigest()
    before=original('features/agent-feature-29k/physical-ttl-before.json');after=original('features/agent-feature-29k/physical-ttl-after.json')
    assert before=={'keys':0,'pttl_ms':[]} and after['keys']==len(after['pttl_ms'])==200
    assert all(0<x<=3600000 for x in after['pttl_ms'])
    container=original('features/agent-feature-29k/materialization-container.json')
    assert container['image']==feature['sdk_image_id'] and container['state']['ExitCode']==0 and not container['state']['Running'] and not container['state']['OOMKilled']
    cpu=original('session.json');gpu=original('gpu-session.json')
    assert cpu['max_run_seconds']==gpu['max_duration_seconds']==28800 and gpu['termination_action']=='STOP' and gpu['status']=='RUNNING'
    gpu_stop=datetime.fromisoformat(gpu['last_start_utc'])+timedelta(seconds=28800)
    cpu_stop=datetime.fromisoformat(cpu['last_start_utc'])+timedelta(seconds=28800)
    assert cpu_stop==datetime.fromisoformat(cpu['auto_stop_utc'])
    times={};acceptances={}
    for role,number in ACCEPTED.items():
        prefix='release/'+role+'/'+str(number)+'/';job_prefix='jenkins/'+role+'/'
        configuration=ET.fromstring(bodies[job_prefix+'config.xml'])
        assert configuration.find('definition/script').text.encode()==(ROOT/'infra/ci/Agents.Jenkinsfile').read_bytes()
        for attempt in range(1,number+1):
            job=original(job_prefix+str(attempt)+'/build.json');expected='SUCCESS' if attempt==number or role=='coordinator' and attempt==2 else 'FAILURE'
            assert job['number']==attempt and not job['building'] and job['result']==expected
            params={p['name']:p['value'] for a in job['actions'] for p in a.get('parameters',[])}
            expected_commit=FIRST if attempt==1 else COMMIT if attempt==number else SECOND
            assert params=={'SOURCE_COMMIT':expected_commit}
            assert bodies[job_prefix+str(attempt)+'/console.txt'].rstrip().endswith(('Finished: '+expected).encode())
        job=original(job_prefix+str(number)+'/build.json');stages=original(job_prefix+str(number)+'/stages.json')
        assert stages['status']=='SUCCESS' and len(stages['stages'])==7 and all(s['status']=='SUCCESS' for s in stages['stages'])
        times[role]={'start_ms':job['timestamp'],'end_ms':job['timestamp']+job['duration']}
        identity=release_identity(role,number,COMMIT);phases={}
        for phase in ['test','build','deploy','verify']:
            phases[phase]=require_receipt(original(prefix+phase+'.json'),identity,phase)
            assert (base/(role+'-'+phase+'.json')).read_bytes()==bodies[prefix+phase+'.json']
        contract=phases['test']['contracts'];assert contract['passed'] and contract['source_digest']==source_digest
        assert b'Ran 14 tests' in bodies[prefix+'contracts.txt'] and bodies[prefix+'contracts.txt'].rstrip().endswith(b'OK')
        controller=original(prefix+'controller.json')['id']
        for phase in ['test','build','deploy','verify']:
            native=original(prefix+phase+'-container.json');state=native['state']
            assert native['image']==controller and native['commit']==COMMIT and state['ExitCode']==0 and not state['Running'] and not state['OOMKilled']
            assert native['memory_bytes']==2*1024**3 and native['nano_cpus']==10**9
        build=phases['build'];deploy=phases['deploy'];accepted=phases['verify']
        assert build['source_digest']==deploy['source_digest']==accepted['source_digest']==source_digest
        for chart,details in build['charts'].items():
            chart_raw=bodies[prefix+details['path']];assert sha256(chart_raw).hexdigest()==details['sha256'];members={}
            with tarfile.open(fileobj=BytesIO(chart_raw)) as package:
                for member in package:
                    if member.isfile():
                        name=Path(member.name).relative_to(chart).as_posix();assert '..' not in Path(name).parts and name not in members
                        members[name]=package.extractfile(member).read()
            assert {n:sha256(v).hexdigest() for n,v in members.items()}==details['packaged_files']
            expected={Path(n).relative_to('infra/charts/'+chart).as_posix() for n in seal['files'] if n.startswith('infra/charts/'+chart+'/')}
            assert set(members)==expected
            for name,body in members.items():
                source=(ROOT/'infra/charts'/chart/name).read_bytes()
                if name=='Chart.yaml':
                    declared=scalar_chart(source)
                    if chart=='iot-agents':declared['appVersion']='0.2.2'
                    assert scalar_chart(body)==declared
                else:assert body==source
        live=original(prefix+'before-deploy.json');ready_agents(live['agents']['items'],policy)
        assert live['feature_seed_uid']==feature['seed_uid'] and live['gpu_session']==gpu
        assert live['gpu_session_sha256']==sha256(bodies['gpu-session.json']).hexdigest()
        lease=feature_lease(feature,datetime.fromisoformat(live['feature_lease']['checked_utc']));assert lease==live['feature_lease']
        assert (gpu_stop-datetime.fromisoformat(lease['checked_utc'])).total_seconds()>=900
        assert ready_agents(original(prefix+'agents-after.json')['items'],policy)
        assert {p['metadata']['name']:p['spec'] for p in original(prefix+'pools-after.json')['items']}=={p['metadata']['name']:p['spec'] for p in live['pools']['items']}
        assert live['model_config']['spec']==original('final-cluster/modelconfig.json')['spec']
        assert all(v['status']=='deployed' for v in deploy['helm_revisions'].values()) and deploy['current_policy']=='0.2.2'
        reply=original(prefix+'agent-request/'+role+'-response.json');transport=original(prefix+'agent-request/'+role+'-transport.json')
        assert transport['http_status']==200 and transport['response_complete']
        if role=='coordinator':
            children={r:{k:original(prefix+'agent-request/'+r+'-'+k+'.json') for k in ['session','tasks']} for r in ['retrieval','drift']}
            checked=verify_coordinator(reply,children,knowledge_root=ROOT/'knowledge')
            assert build['application'] is None and contract['unchanged_mcp_quality_cache'] is None and not accepted['native_mcp_probe']
        else:
            checked=verify_specialist(reply,role,knowledge_root=ROOT/'knowledge');assert accepted['native_mcp_probe']
            quality=api_quality(original(prefix+'api-tests/coverage.json'),ET.fromstring(bodies[prefix+'api-tests/junit.xml']))
            assert quality==phases['test']['fresh_api']
            cached=contract['unchanged_mcp_quality_cache'];assert cached['source_bindings']==cache_bindings and cached['fresh_mutation_run'] is False
            score=json.loads((ROOT/'docs/evidence/mcp-quality/run/strict-score.json').read_bytes())
            assert cached['counts']==score['counts'] and sum(cached['counts'].values())==850 and cached['counts']['killed']==681
            assert cached['strict_mutation_score_percent']==100*681/850>80
            test_image=original(prefix+'test-image.json');app=build['application']
            for details,parent,tests in [(test_image,MCP_TESTS,True),(app,MCP_RUNTIME,False)]:
                assert details['parent_image_id']==parent and details['revision_label']==COMMIT
                assert details['packaged_sha256']=={n:seal['files'][n] for n in packaged_paths(seal,tests)}
            native=original(prefix+'api-tests-container.json');assert native['image']==test_image['image_id'] and native['commit']==COMMIT
            assert native['state']['ExitCode']==0 and not native['state']['Running'] and not native['state']['OOMKilled']
            assert native['memory_bytes']==2*1024**3 and native['nano_cpus']==10**9
            export='image-export/'+role+'/'+str(number)+'/'
            image_manifest=original(export+'manifest.json');assert len(image_manifest)==1
            config=bodies[export+image_manifest[0]['Config']];config_value=json.loads(config)
            assert app['imported_config_digest']=='sha256:'+sha256(config).hexdigest()
            assert config_value['config']['User']=='10001:10001' and config_value['config']['Labels']['org.opencontainers.image.revision']==COMMIT
            assert check_pods(original(prefix+'mcp-pods-after.json')['items'],app['image'],app['imported_config_digest'],'mcp')==original(prefix+'mcp-pod-uids.json')
            probe=original(prefix+'native-mcp-probe.json');tool_probe(probe,role,identity['release'])
            assert bodies[prefix+'native-mcp-probe.exit']==b'0\n'
            observed=[json.loads(line)['observed_call'] for line in bodies[prefix+'native-mcp-probe.stderr.txt'].splitlines()]
            assert observed==probe['calls']
        assert checked==original(prefix+'agent-acceptance.json')==accepted['actual_agent_acceptance']
        acceptances[role]=checked
        end=datetime.fromtimestamp(times[role]['end_ms']/1000,timezone.utc)
        assert end<gpu_stop and end<cpu_stop and end<datetime.fromisoformat(feature['lease_expires_utc'])
    assert times['coordinator']['start_ms']>max(times[r]['end_ms'] for r in ['retrieval','drift'])
    for role in ACCEPTED:
        first='release/'+role+'/1/'
        assert b'ModuleNotFoundError' in bodies[first+'contracts.txt'] and b'rag.ci_metadata' in bodies[first+'contracts.txt']
        assert first+'deploy.json' not in bodies
        if role=='coordinator':continue
        second='release/'+role+'/2/';third='release/'+role+'/3/'
        assert b'The real feature lease is too short' in bodies[second+'deploy.txt']
        assert second+'deployment-plan.json' not in bodies and second+'deploy.json' not in bodies
        assert original(third+'deploy.json')['passed'] and b'JSONDecodeError: Extra data' in bodies[third+'verify.txt']
        text=bodies[third+'native-mcp-probe.txt'].decode();values=[];decoder=json.JSONDecoder()
        while text.strip():
            text=text.lstrip();value,position=decoder.raw_decode(text);values.append(value);text=text[position:]
        tool_probe(values[-1],role,'llm-'+role+'-jenkins-3');assert [v['observed_call'] for v in values[:-1]]==values[-1]['calls']
        rollback=original(third+'rollback.json');plan=original(third+'deployment-plan.json')
        assert rollback['passed'] and set(rollback['actions'])=={'iot-agents','iot-'+role+'-mcp'}
        for name,action in rollback['actions'].items():
            assert action=={'restored_revision':plan['before'][name]['revision']}
            assert b'Rollback was a success!' in bodies[third+'rollback-'+name+'.txt']
    ready_agents(original('final-cluster/sandboxagents.json')['items'],policy)
    ui=read(base,'ui/index.json');assert ui['original_screenshots'] and len(ui['entries'])==3
    for entry in ui['entries']:
        assert entry['pixels_edited'] is False and (base/'ui'/entry['image']).read_bytes().startswith(b'\xff\xd8\xff')
        role=entry['image'].split('-')[0];assert entry['accepted_build']==ACCEPTED[role]
        text=(base/'ui'/entry['accessibility']).read_text(encoding='utf-8')
        assert 'iot-llm-'+role in text and '#'+str(ACCEPTED[role]) in text and 'Verify real tool calls' in text
    for name in ['mcp-quality','mcp-quality-native']:
        subprocess.run([sys.executable,'-B','-S',str(ROOT/'scripts'/('verify-'+name+'.py'))],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
    return {'passed':True,'public_payloads':count,'native_payloads':1605,'source_commit':COMMIT,'source_files':362,
            'accepted_builds':ACCEPTED,'fresh_api_cases_per_mcp_job':132,'configuration_tests_per_job':14,
            'mutation_scope':'Source-bound cached681/850; timeouts and worker errors retained. No fresh mutation run.',
            'native_role_acceptances':acceptances,'failed_builds_retained':7,'coursework_complete':False}


if __name__=='__main__':print(json.dumps(verify(),indent=2))
