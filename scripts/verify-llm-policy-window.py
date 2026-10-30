"""Reconcile original synthetic policy requests, native ledger/logs, GPU counters and the repaired Grafana display offline."""
from collections import Counter
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import json,re,sys,tarfile
from integration_evidence import ROOT,capsule,read
sys.path.insert(0,str(ROOT))
from llm.policy_window import CASES,request_body,summarize
from llm_observability.contracts import StreamObservation
MANIFEST='18a1252b502a655b3d9727c319ea647e94f9eccc43d15d1aeb9af9ea13a69322'


def unpack(raw):
    """Authenticate every original native member without extracting archive paths or following links."""
    bodies={}
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            assert member.isdir() or member.isfile()
            if not member.isfile():continue
            name=PurePosixPath(member.name).relative_to('llm-policy-window-capture-30a')
            assert not name.is_absolute() and '..' not in name.parts and member.size<10000000 and name.as_posix() not in bodies
            bodies[name.as_posix()]=archive.extractfile(member).read()
    assert sha256(bodies['manifest.json']).hexdigest()=='ac3bfbb797edfa3f6a47aee58e682d5cc1824e9d08f87012bb42dd57a1f82892'
    inventory=json.loads(bodies['manifest.json']);assert len(inventory['files'])==150 and set(bodies)==set(inventory['files'])|{'manifest.json'}
    for name,pin in inventory['files'].items():assert len(bodies[name])==pin['bytes'] and sha256(bodies[name]).hexdigest()==pin['sha256'],name
    return bodies


def counter(raw,name,**labels):
    """Read original counter samples independently of the workload's receipt summary."""
    total=0.
    for line in raw.decode().splitlines():
        match=re.fullmatch(re.escape(name)+r'(?:\{(.*)\})? ([0-9.eE+\-]+)',line)
        if not match:continue
        actual=dict(re.findall(r'([a-zA-Z_][a-zA-Z_0-9]*)="([^"\\]*)"',match[1] or ''))
        if all(actual.get(k)==v for k,v in labels.items()):total+=float(match[2])
    return total


def verify():
    """Require all twelve admissions, separate overlapping hits and keep inference, experiment and display clocks distinct."""
    base,count=capsule('llm-policy-window',MANIFEST);assert count==14
    raw=(base/'native-receipts.tar.gz').read_bytes();metadata=read(base,'native-archive.json')
    assert len(raw)==metadata['archive_bytes']==541219 and sha256(raw).hexdigest()==metadata['archive_sha256']=='f9c7e5580fb6917dc7be675361612533de98bdbeb0bdb04a0dc474e2fed6b6f3'
    assert metadata['current_native_credentials_scanned'] and metadata['pc_actual_gpu_join_credential_scanned']
    assert not metadata['recompressed_or_rewritten_on_pc'] and not metadata['workloads_rerun_for_capture']
    bodies=unpack(raw);native=lambda name:json.loads(bodies[name]);out='native/policy-window-30a/';view='native/policy-window-view-30b/'
    for folder in ['policy-window-24efae5c','policy-window-view-752bae3d','policy-window-view-4de60f36']:
        prefix='source/'+folder+'/';seal=native(prefix+'manifest.json')
        assert len(seal['files'])==(4 if folder=='policy-window-24efae5c' else 2)
        for name,pin in seal['files'].items():
            original=bodies[prefix+'tree/'+name];assert sha256(original).hexdigest()==pin
            if folder!='policy-window-view-752bae3d':
                current=(ROOT/name).read_bytes();assert current==original or current.replace(b'\r\n',b'\n')==original,name
    assert native(out+'cases.json')==CASES
    report=native(out+'verification.json');assert report['passed'] and report==read(base,'window-verification.json')
    start=native(out+'window-start.json');end=native(out+'window-end.json')
    assert start['paced_seconds']==3 and start['admissions']==12 and start['started_utc']==report['started_utc'] and end['finished_utc']==report['finished_utc']
    seconds=end['elapsed_seconds'];assert 36<=seconds<37
    assert abs((datetime.fromisoformat(end['finished_utc'])-datetime.fromisoformat(start['started_utc'])).total_seconds()-seconds)<.05
    rows=native(out+'requests.json');before=native(out+'ledger-before.json');after=native(out+'ledger-after.json')
    old={v['event_id'] for v in before if v['kind']=='request'}
    facts={v['event_id']:v['facts'] for v in after if v['kind']=='request' and v['event_id'] not in old}
    assert len(facts)==12 and set(facts)=={v['request_id'] for v in rows}
    logged=[]
    for line in bodies[out+'observer-timestamped.log'].decode().splitlines():
        stamp,_,text=line.partition(' ')
        try:value=json.loads(text)
        except ValueError:continue
        if value.get('event')=='model_request' and value.get('request_id') in facts:logged.append({'timestamp':stamp,**value})
    assert len(logged)==12 and logged==native(out+'request-logs.json') and {v['request_id'] for v in logged}==set(facts)
    log_index={v['request_id']:v for v in logged};provider=Counter();denied=0
    for i,row in enumerate(rows):
        assert row['case_index']==i and row['case_id']==CASES[i]['id'] and row['request_body']==request_body(i)==native(out+'case-'+str(i)+'-request.json')
        wire=bodies[out+'case-'+str(i)+'.wire'];assert sha256(wire).hexdigest()==row['wire_sha256']
        fact=facts[row['request_id']];assert row['policy_categories']==fact['policy_categories']==CASES[i]['expected_categories']
        assert fact['status']==row['status'] and fact['usage']==row['usage'] and fact['streaming'] and fact['agent']=='direct'
        assert all(log_index[row['request_id']][k]==v for k,v in fact.items())
        stamp=datetime.fromisoformat(log_index[row['request_id']]['timestamp'].replace('Z','+00:00'))
        assert datetime.fromisoformat(start['started_utc'])<=stamp<=datetime.fromisoformat(end['finished_utc'])
        assert 0<fact['duration_seconds']<=row['elapsed_seconds']+.05
        if row['policy_categories']:
            denied+=1;assert row['status']==422 and fact['result']=='policy_rejected' and fact['usage'] is None and fact['ttft_seconds'] is None
            assert json.loads(wire)['error']['code']=='prompt_policy_rejected' and not row['inference_complete']
        else:
            observer=StreamObservation();offset=0;last=0.
            for chunk in row['chunks']:
                assert chunk['offset']==offset and last<=chunk['elapsed_seconds']<=row['elapsed_seconds']
                observer.feed(wire[offset:offset+chunk['bytes']],chunk['elapsed_seconds']);offset+=chunk['bytes'];last=chunk['elapsed_seconds']
            observed=observer.finish(row['elapsed_seconds']);assert offset==len(wire) and observed['complete']
            assert observed['usage']==row['usage'] and observed['ttft_seconds']==row['ttft_seconds']
            assert 0<fact['ttft_seconds']<=fact['duration_seconds'] and row['status']==200 and fact['result']=='ok'
            events=[json.loads(v[5:]) for v in wire.decode().splitlines() if v.startswith('data:') and v[5:].strip()!='[DONE]']
            assert {v['model'] for v in events if 'model' in v}=={'iot-qwen2.5-7b'};provider.update(row['usage'])
    summary=summarize(rows,seconds);assert summary==report['summary']==native(out+'summary.json')==read(base,'window-summary.json')
    assert denied==summary['blocked_prompts']==9 and summary['allowed_prompts']==3 and summary['total_category_hits']==12
    assert provider=={'prompt_tokens':108,'completion_tokens':6,'total_tokens':114}
    assert summary['blocked_prompts_per_observed_minute']==9*60/seconds
    a=bodies[out+'observer-before.prom'];b=bodies[out+'observer-after.prom']
    for outcome,expected in [('allowed',3),('blocked',9)]:assert counter(b,'iot_llm_policy_checks_total',outcome=outcome)-counter(a,'iot_llm_policy_checks_total',outcome=outcome)==expected
    for category in ['hardware_command','pii_email','pii_phone','secret_literal']:
        assert counter(b,'iot_llm_policy_hits_total',category=category)-counter(a,'iot_llm_policy_hits_total',category=category)==summary['category_hits'][category]==3
    generated=counter(bodies[out+'model-after.prom'],'llamacpp:tokens_predicted_total')-counter(bodies[out+'model-before.prom'],'llamacpp:tokens_predicted_total')
    assert generated==provider['completion_tokens']==report['native_generated_token_delta']==6
    pods_before=native(out+'pods-before.json');pods_after=native(out+'pods-after.json');assert set(pods_before)==set(pods_after) and len(pods_before)==3
    for instance,pod in pods_before.items():
        now=pods_after[instance];assert now['metadata']['uid']==pod['metadata']['uid'] and now['spec']==pod['spec']
        assert {v['name']:v['restartCount'] for v in pod['status']['containerStatuses']}=={v['name']:v['restartCount'] for v in now['status']['containerStatuses']}
    dashboard=native(view+'verification.json');assert dashboard==read(base,'dashboard-verification.json') and dashboard['passed'] and dashboard['numeric_panel_matches']==8
    planned=native(view+'dashboard-planned.json');actual=native(view+'dashboard-native.json')['dashboard'];assert actual['panels']==planned['panels'] and actual['time']==planned['time']
    clocks=native(view+'display-clocks.json');display_end=datetime.fromisoformat(actual['time']['to']).timestamp()
    assert display_end==clocks['display_end_unix']>datetime.fromisoformat(clocks['original_window_finished_utc']).timestamp()
    first=native('native/policy-window-view-30a/dashboard-planned.json')
    first_publish=native('native/policy-window-view-30a/batch-gauge-publication.json')
    assert datetime.fromisoformat(first['time']['to'])<datetime.fromisoformat(first_publish['published_utc'])
    assert first['panels']==planned['panels'] and native(view+'batch-gauge-publication.json')['new_model_requests']==0
    old_config=native(view+'prometheus-before.json');new_config=native(view+'prometheus-planned.json');assert old_config==new_config
    expected=[12,9,12,seconds,9*60/seconds];panels=native(view+'panels.json');assert len(panels)==8
    for panel in panels:
        label=view+'panel-'+str(panel['id'])+'-';direct=native(label+'prometheus.json');proxy=native(label+'grafana.json')
        assert direct['status']==proxy['status']=='success' and direct['data']==proxy['data'] and direct['data']['result']
        result=direct['data']['result']
        if panel['id']<=6:
            # Prometheus exposes query timestamps at millisecond precision; the original wall clock retains microseconds.
            assert len(result)==1 and 0<=display_end-float(result[0]['value'][0])<.001 and float(result[0]['value'][1])==expected[panel['id']-2]
        elif panel['id'] in [7,8]:
            target=summary['category_hits' if panel['id']==7 else 'category_hits_per_observed_minute']
            assert {v['metric']['category']:float(v['value'][1]) for v in result}==target
        else:assert max(float(v[1]) for series in result for v in series['values'])>0
    units=[('iot-policy-window-30a','d3c4455acc30463987220df6998b02ec'),('iot-policy-window-view-30a','d7a9e284adc24db4b5c3a40135dfb63f'),('iot-policy-window-view-30b','f6af5d06609544e78fc2a9c6ca41862a')]
    for unit,identity in units:
        journal=[json.loads(v) for v in bodies['units/'+unit+'/journal.stdout'].splitlines()];own=[v for v in journal if v.get('INVOCATION_ID')==identity]
        assert any('Started ' in v.get('MESSAGE','') for v in own) and any('Deactivated successfully.' in v.get('MESSAGE','') for v in own)
    for photo in read(base,'ui/index.json')['files']:
        body=(base/'ui'/photo['file']).read_bytes();assert body.startswith(b'\xff\xd8\xff') and len(body)==photo['bytes'] and sha256(body).hexdigest()==photo['sha256']
    assert 'No data' in (base/'ui/before-time-fix.ax.txt').read_text() and 'No data' not in (base/'ui/results.ax.txt').read_text()
    assert '15.0' in (base/'ui/results.ax.txt').read_text()
    print(json.dumps({'passed':True,'native_payloads':150,'actual_admissions':12,'unique_blocked_prompts':9,'overlapping_category_hits':12,
        'allowed_provider_tokens':dict(provider),'native_generated_token_delta':generated,'numeric_dashboard_matches':8,
        'original_time_range_defect_retained':True,'scope':'A fixed synthetic pattern-policy window, not universal detection or production prevalence.'},indent=2))


if __name__=='__main__':verify()
