"""Authenticate the complete observer quality history and reconcile the ready native GPU canary with original wire, clocks, logs and counters."""
from collections import Counter
from datetime import datetime,timedelta,timezone
from hashlib import sha256
from importlib.util import module_from_spec,spec_from_file_location
from io import BytesIO
from pathlib import PurePosixPath
from xml.etree import ElementTree
import json,math,re,tarfile
from integration_evidence import ROOT,capsule,read,exited

MANIFEST='f1fc4d7534ac24c1774f33f0900a0431b3c92723d431f162bf91897ea4fa960f'


def verify():
    """Verify retained facts offline; this does not claim that a running service or agent dashboard is ready now."""
    base,count=capsule('llm-observer',MANIFEST);meta=read(base,'native-archive.json');raw=(base/'native-receipts.tar.gz').read_bytes()
    assert count==6 and len(raw)==meta['archive_bytes']==2087521
    assert sha256(raw).hexdigest()==meta['archive_sha256']=='43da76255040e35837db73df20a4cb6a692cf588dd4d8688558c83fc4ad81103'
    assert not meta['recompressed_or_rewritten_on_pc'] and not meta['workloads_rerun_for_capture']
    assert meta['actual_native_credentials_scanned'] and meta['pc_actual_gpu_join_credential_scanned']
    assert meta['additional_native_scan']['passed'] and meta['additional_native_scan']['actual_service_vault_gateway_approle_credentials_scanned']
    bodies={}
    with tarfile.open(fileobj=BytesIO(raw)) as packet:
        for member in packet:
            assert member.isdir() or member.isfile()
            if not member.isfile():continue
            name=PurePosixPath(member.name).relative_to('llm-observer-quality-capture-29c').as_posix()
            assert '..' not in PurePosixPath(name).parts and name not in bodies and member.size<20_000_000
            bodies[name]=packet.extractfile(member).read()
    inventory=json.loads(bodies['manifest.json'])
    assert sha256(bodies['manifest.json']).hexdigest()==meta['original_inventory_sha256']=='99f136bd7563325d7f307e558b37649daf9a03da12c521b2a7cfc4d863fe7768'
    assert len(inventory['files'])==meta['native_payloads']==894 and set(bodies)==set(inventory['files'])|{'manifest.json'}
    for n,item in inventory['files'].items():assert len(bodies[n])==item['bytes'] and sha256(bodies[n]).hexdigest()==item['sha256'],n
    def native(name):
        """Decode a retained document only after authenticating every original archive byte."""
        return json.loads(bodies[name])
    for public,n in [('quality-verification.json','quality/29f/verification.json'),('canary-verification.json','canary/29c/verification.json'),
                     ('probe-verification.json','probe/verification.json'),('generations.json','probe/generated.json')]:
        assert (base/public).read_bytes()==bodies[n]
    for prefix in ['29a','29b','29c','29d','29e','29f']:
        seal=native('quality-source/'+prefix+'/manifest.json');assert seal==native('quality/'+prefix+'/source.json')
        assert seal['format']=='iot-llm-observer-source-v1'
        for n,digest in seal['files'].items():assert sha256(bodies['quality-source/'+prefix+'/tree/'+n]).hexdigest()==digest
    quality=native('quality/29f/verification.json');proof='quality/29f/quality-proof/run/';result=native(proof+'verification.json')
    assert quality['passed'] and result==quality['quality'] and result['fresh_mutation_run']
    assert quality['source_commit']==result['commit']=='97ba77b6d538b816b6304c17031090e3820a3fa0' and result['source_files']==26
    seal=native('quality-source/29f/manifest.json');runtime={n:d for n,d in seal['files'].items() if n.startswith('llm_observability/')}
    assert len(runtime)==6
    for n,d in runtime.items():assert sha256((ROOT/n).read_bytes()).hexdigest()==d,n
    for label in ['runtime','test']:
        image=quality['images'][label];assert image['metadata']['packages']['prometheus-client']=='0.24.1'
        if label=='runtime':assert image['metadata']['files']==runtime
    test=native('quality/29f/quality-container.json');exited(test)
    assert test['Image']==quality['images']['test']['image_id'] and test['HostConfig']['Memory']==6*1024**3 and test['HostConfig']['NanoCpus']==5_000_000_000
    coverage=native(proof+'coverage.json');assert coverage['totals']==result['coverage']
    assert set(coverage['files'])==set(runtime) and coverage['totals']['excluded_lines']==0
    statements=sum(v['summary']['num_statements'] for v in coverage['files'].values())
    covered=sum(v['summary']['covered_lines'] for v in coverage['files'].values())
    branches=sum(v['summary']['num_branches'] for v in coverage['files'].values())
    hit=sum(v['summary']['covered_branches'] for v in coverage['files'].values())
    assert (covered,statements,hit,branches)==(492,492,253,256)
    junit=ElementTree.fromstring(bodies[proof+'junit.xml']);suites=junit.findall('testsuite')
    assert sum(int(v.attrib['tests']) for v in suites)==398 and len(junit.findall('.//testcase'))==93
    assert all(int(v.attrib[n])==0 for v in suites for n in ['errors','failures','skipped'])
    assert b'93 passed, 305 subtests passed' in bodies[proof+'tests.txt']
    spec=spec_from_file_location('observer_quality',ROOT/'scripts/test-llm-observer.py');gate=module_from_spec(spec);spec.loader.exec_module(gate)
    stats=native(proof+'mutmut-cicd-stats.json');codes=native(proof+'mutation-raw-exits.json')
    meta_codes={}
    for n in bodies:
        if n.startswith(proof+'mutation-meta/') and n.endswith('.meta'):
            item=native(n);assert not item.get('type_check_error_by_key')
            assert not set(meta_codes)&set(item['exit_code_by_key']);meta_codes.update(item['exit_code_by_key'])
    assert meta_codes==codes and Counter(codes.values())=={1:2348,0:584}
    score=gate.mutation_gate(stats,bodies[proof+'mutmut-all-results.txt'].decode(),codes)
    assert stats==result['mutation'] and score==result['strict_mutation_score_percent'] and 80<score<81
    # Failed predecessors remain original failures; interrupted C has no completed mutation score.
    assert bodies['quality/29a/runtime-build.exit']!=b'0\n'
    for run,killed,total in [('29b',1378,2151),('29d',2243,2929),('29e',2305,2932)]:
        st=native('quality/'+run+'/quality-proof/run/mutmut-cicd-stats.json')
        assert st['killed']==killed and st['total']==total and 100*killed/total<=80
        assert bodies['quality/'+run+'/quality.exit']!=b'0\n'
    assert 'quality/29c/quality-proof/run/verification.json' not in bodies
    interrupted=native('quality/29c/quality-container.json');exited(interrupted,137)
    assert native('canary/29a/image-cri.stdout')['status']['repoDigests']==[]
    assert b'Kubernetes cluster unreachable' in bodies['canary/29b/helm-install.stderr']
    assert bodies['canary/29b/helm-install.exit']==b'1\n' and bodies['canary/29c/helm-install.exit']==b'0\n'
    accepted=native('canary/29c/verification.json');assert accepted['passed'] and not accepted['routing_changed'] and accepted['packaged_files']==runtime
    for prefix in ['8d28efae','d5c61621','7aa14195','5dfdc89a']:
        source=native('canary-source/'+prefix+'/manifest.json');assert source['commit'].startswith(prefix) and len(source['files'])==15
        for n,digest in source['files'].items():assert sha256(bodies['canary-source/'+prefix+'/tree/'+n]).hexdigest()==digest
    for label,invocation in [('canary/29c','d8c87f838b914546a9bc0e9d37d33282'),('probe','25776077f72649f08bc25b19af6af61d')]:
        entries=[json.loads(line) for line in bodies[label+'/journal.stdout'].splitlines()]
        own=[e for e in entries if e.get('INVOCATION_ID')==invocation]
        assert any('Started ' in e.get('MESSAGE','') for e in own) and any('Deactivated successfully.' in e.get('MESSAGE','') for e in own)
        assert not any('Failed with result' in e.get('MESSAGE','') for e in own)
    index=native('canary/29c/image-save-index.json');export=native('canary/29c/image-export.json')
    assert 'sha256:'+sha256(bodies['canary/29c/image-oci-index.json']).hexdigest()==export['index_digest']
    assert any(v['digest']==export['index_digest'] for v in index['manifests'])
    assert 'sha256:'+sha256(bodies['canary/29c/image-save-config.json']).hexdigest()==accepted['config_digest']==export['config_digest']
    assert accepted['image']==export['image_ref']=='docker.io/library/iot-llm-observer@'+export['index_digest']
    imported=native('canary/29c/image-digest-cri-0.stdout')['status']
    assert imported['id']==accepted['config_digest'] and imported['repoDigests']==[accepted['image']]
    for name in ['pool','router','control','gpu','agents','model']:
        before=native('canary/29c/before-'+name+'.json');after=native('canary/29c/after-'+name+'.json')
        if name=='agents':assert {v['metadata']['uid']:v['spec'] for v in before['items']}=={v['metadata']['uid']:v['spec'] for v in after['items']}
        else:assert before['metadata']['uid']==after['metadata']['uid'] and before['spec']==after['spec']
    pod=native('canary/29c/pods.json')['items'][0];assert pod['metadata']['uid']==accepted['pod_uid']
    assert all(c['ready'] and c['state'].get('running') for c in pod['status']['containerStatuses'])
    assert native('canary/29c/service.json')['spec']['type']=='ClusterIP'
    assert native('canary/29c/secret-identity.json')['uid']==native('canary/29b/secret-identity.json')['uid']
    assert native('canary/29c/retained-install-inputs.json')['existing_inputs_unchanged']
    for run in ['29a','29b','29c']:
        vault=native('canary/'+run+'/vault-consumer.json');assert vault['token_revoked'] and vault['same_credential_bytes'] and vault['lease']['lease_duration']==300
    report=native('probe/verification.json');rows=native('probe/ledger-after.json');requests=native('probe/all-requests.json')
    assert report['passed'] and not report['routing_changed'] and report['pod_uid']==accepted['pod_uid']
    assert native('probe/ledger-before.json')==[] and len(rows)==report['request_rows']==9
    facts={v['event_id']:v['facts'] for v in rows};assert len(facts)==9 and all(v['kind']=='request' for v in rows)
    outcomes=dict(Counter(v['facts']['result'] for v in rows));assert outcomes==report['outcomes']=={'ok':3,'policy_rejected':4,'invalid_request':2}
    assert len(requests)==12 and len({v['case'] for v in requests})==12
    bycase={v['case']:v for v in requests}
    for r in requests:
        wire=bodies['probe/'+r['case']+'-response.bin'];assert len(wire)==r['bytes'] and sha256(wire).hexdigest()==r['sha256']
        offset=0;last=0
        for chunk in r['chunks']:
            assert chunk['offset']==offset and chunk['bytes']>0 and last<=chunk['elapsed_seconds']<=r['full_seconds']
            offset+=chunk['bytes'];last=chunk['elapsed_seconds']
        assert offset==len(wire) and 0<=r['headers_seconds']<=r['full_seconds']
    assert bycase['unauthorized']['status']==401 and bycase['malformed']['status']==400
    for name in ['pii_email','pii_phone','secret_literal','hardware_command']:
        r=bycase[name];assert r['status']==422
        assert native('probe/'+name+'-response.bin')['error']['code']=='prompt_policy_rejected'
        assert facts[r['headers']['x-iot-request-id']]['policy_categories']==[name]
    generated=native('probe/generated.json');assert generated==report['generations'] and len(generated)==3
    for i,item in enumerate(generated):
        r=bycase['generation-'+str(i)];wire=bodies['probe/generation-'+str(i)+'-response.bin'];fact=facts[item['request_id']]
        assert r['status']==200 and item['request_id']==r['headers']['x-iot-request-id'] and item['client_full_seconds']==r['full_seconds']
        if item['streaming']:
            assert b'data: [DONE]' in wire;events=[];position=0;first_end=None
            for line in wire.splitlines(keepends=True):
                position+=len(line)
                if not line.startswith(b'data: ') or line.strip()==b'data: [DONE]':continue
                event=json.loads(line[6:]);events.append(event)
                if first_end is None and any(v.get('delta',{}).get('content') for v in event['choices']):first_end=position
            usages=[e['usage'] for e in events if e.get('usage')];assert len(usages)==1 and first_end is not None
            usage=usages[0];clock=next(c['elapsed_seconds'] for c in r['chunks'] if c['offset']+c['bytes']>=first_end)
            assert clock==item['client_ttft_seconds'] and 0<=fact['ttft_seconds']<=clock+.01
        else:
            usage=json.loads(wire)['usage'];assert item['client_ttft_seconds'] is None and fact['ttft_seconds'] is None
        selected={k:usage[k] for k in ['prompt_tokens','completion_tokens','total_tokens']}
        assert selected==item['usage']==fact['usage'] and selected['prompt_tokens']+selected['completion_tokens']==selected['total_tokens']
        assert fact['duration_seconds']<=item['client_full_seconds']+.01 and fact['status']==200 and fact['result']=='ok'
    metrics=bodies['probe/metrics-response.bin'].decode()
    for name,field in [('input','prompt_tokens'),('output','completion_tokens'),('total','total_tokens')]:
        sample=re.findall(r'^iot_llm_reported_tokens_total\{[^\n]*type="'+name+r'"\} ([^\n]+)$',metrics,re.M)
        assert len(sample)==1 and float(sample[0])==sum(v['usage'][field] for v in generated)
    for name in ['hardware_command','pii_email','pii_phone','secret_literal']:
        assert 'iot_llm_policy_hits_total{category="'+name+'"} 1\n' in metrics
    assert 'iot_llm_policy_checks_total{outcome="blocked"} 4\n' in metrics
    assert 'iot_llm_policy_checks_total{outcome="allowed"} 3\n' in metrics
    logrows=[json.loads(line.split(b' ',1)[1]) for line in bodies['probe/observer-logs.txt'].splitlines() if b' {"event":"model_request"' in line]
    assert len(logrows)==9 and {v['request_id'] for v in logrows}==set(facts)
    for item in logrows:
        assert set(item)==set(facts[item['request_id']])|{'event','request_id','trace_id','span_id'}
        assert all(item[k]==v for k,v in facts[item['request_id']].items())
    now=datetime.fromisoformat(report['checked_utc']);cpu=native('probe/cpu-session.json');gpu=native('probe/gpu-session.json')
    assert now<datetime.fromisoformat(cpu['auto_stop_utc']) and now<datetime.fromisoformat(gpu['last_start_utc'])+timedelta(seconds=28800)
    return {'passed':True,'native_payloads':894,'native_junit_reported_tests':398,'native_test_cases':93,'native_subtests':305,
        'statement_percent':100*covered/statements,'branch_percent':100*hit/branches,'fresh_mutation_percent':score,
        'mutants':len(codes),'raw_failing_test_kills':2348,'ready_source_bound_canary':True,'real_model_posts':9,'outcomes':outcomes,
        'reported_tokens':sum(v['usage']['total_tokens'] for v in generated),'client_ttft_seconds':[v['client_ttft_seconds'] for v in generated],
        'scope':'Original source-bound quality and separate private GPU canary only. Native clock measurements remain original observations; no active route, agent trace, load or dashboard acceptance is inferred.'}


if __name__=='__main__':print(json.dumps(verify(),indent=2))
