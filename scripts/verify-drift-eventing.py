"""Audit native Eventing CI, production/reference lineage, exact delivery/replay and original paced load reports offline."""
from git_sources import historical_blob
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from xml.etree import ElementTree
import csv,io,json,math,re,subprocess
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'docs/evidence/drift-eventing-linux'


def read(name):
    """Decode retained native JSON after its manifest is checked."""
    return json.loads((BASE/name).read_bytes())


def counts(text,status='204'):
    """Read the fixed endpoint's actual SDK histogram count, without counting probes or scrapes."""
    return sum(float(v) for a,v in re.findall(r'^http_server_duration_milliseconds_count\{([^\n]+)\} ([0-9.e+]+)$',text,re.M)
               if 'http_target="/v1/events/drift"' in a and 'http_status_code="'+status+'"' in a)


def canonical(value):
    """Use the published stable encoding to independently bind reference and request identities."""
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def identities(state):
    """Compare the same running non-terminating data-path Pod identities in both snapshots."""
    return {kind:sorted(p['metadata']['uid'] for p in rows['items'] if not p['metadata'].get('deletionTimestamp') and p['status']['phase']=='Running') for kind,rows in state.items()}


def main():
    """Recompute every reported fact from raw evidence and bind actual bytes to committed source."""
    manifest=read('manifest.json');actual={p.relative_to(BASE).as_posix() for p in BASE.rglob('*') if p.is_file() and p!=BASE/'manifest.json'}
    assert actual==set(manifest['files'])
    for name,entry in manifest['files'].items():
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        raw=(BASE/name).read_bytes();assert len(raw)==entry['bytes'] and sha256(raw).hexdigest()==entry['sha256'],name
    native=read('native-manifest.json');assert all(manifest['files'][n]==v for n,v in native['files'].items())
    assert sum(v['bytes'] for v in native['files'].values())==native['payload_bytes']
    commit=manifest['ci_source_commit'];source=read('sources/'+commit+'.json');assert len(source['files'])==106
    for path in sorted((BASE/'sources').glob('*.json')):
        if path.name=='actual-workspace-after.json':continue
        seal=json.loads(path.read_bytes())
        for name,wanted in seal['files'].items():
            raw=historical_blob(ROOT, seal['commit']+':'+name);assert sha256(raw).hexdigest()==wanted
    assert read('sources/actual-workspace-after.json')['files']==source['files']
    for number in [1,2,3]:
        build=read(f'jenkins/build-{number}.json');assert build['number']==number and build['building'] is False and build['result']==('SUCCESS' if number==3 else 'FAILURE')
    build=read('jenkins/build-3.json');assert build['duration']==143427
    parameters={p['name']:p['value'] for a in build['actions'] for p in a.get('parameters',[])}
    assert parameters=={'SOURCE_COMMIT':commit,'MODEL_VERSION':'6'}
    stages=read('jenkins/build-3-stages.json');assert stages['status']=='SUCCESS' and len(stages['stages'])==8 and all(s['status']=='SUCCESS' for s in stages['stages'])
    assert 'Finished: SUCCESS' in (BASE/'jenkins/build-3-console.txt').read_text(encoding='utf-8')
    assert 'FileNotFoundError' in (BASE/'jenkins/build-2-console.txt').read_text(encoding='utf-8') and '/usr/local/bin/k3s' in (BASE/'jenkins/build-2-console.txt').read_text(encoding='utf-8')
    first=read('failed-ci/1/tests/gate/mutmut-cicd-stats.json');assert (first['killed'],first['survived'],first['total'])==(37,15,52)
    assert not (BASE/'failed-ci/1/build.json').exists() and not (BASE/'failed-ci/2/plan.json').exists()
    gate=read('current/tests/gate/verification.json');coverage=read('current/tests/gate/coverage.json');stats=read('current/tests/gate/mutmut-cicd-stats.json')
    assert gate['passed'] is True and gate['commit']==commit and gate['coverage']==coverage['totals']
    assert set(coverage['files'])=={'services/drift_api/app.py','services/drift_api/events.py'}
    c=coverage['totals'];assert (c['covered_lines'],c['num_statements'],c['covered_branches'],c['num_branches'],c['excluded_lines'])==(65,65,10,10,0)
    assert (stats['killed'],stats['survived'],stats['total'])==(103,16,119)
    assert all(stats[k]==0 for k in ['no_tests','skipped','suspicious','timeout','check_was_interrupted_by_user','segfault'])
    rows=re.findall(r'^\s*(\S+): (killed|survived)\s*$',(BASE/'current/tests/gate/mutmut-all-results.txt').read_text(encoding='utf-8'),re.M)
    assert len(rows)==len({n for n,_ in rows})==119 and sum(s=='killed' for _,s in rows)==103
    assert {n.split('.x')[0] for n,_ in rows}=={'services.drift_api.app','services.drift_api.events'}
    assert math.isclose(gate['mutation_score_percent'],103/119*100) and gate['mutation_score_percent']>80
    junit=ElementTree.parse(BASE/'current/tests/gate/junit.xml').getroot();suites=list(junit.iter('testsuite'))
    assert len(list(junit.iter('testcase')))==gate['regression_main_testcase_elements']==101
    assert sum(int(s.attrib['tests']) for s in suites)==gate['regression_suite_reported_tests']==121
    assert all(int(s.attrib.get(k,0))==0 for s in suites for k in ['errors','failures','skipped'])
    for name in ['select','tests','deploy','verify']:
        receipt=read('current/'+name+'-container.json');assert receipt['commit']==commit
        assert receipt['state']['ExitCode']==0 and receipt['state']['OOMKilled'] is False and receipt['state']['Running'] is False
    selected=read('current/selection.json');pin=read('bundle/model-pin.json');portable=read('bundle/portable.json');reference=read('current/reference.json')
    assert selected['commit']==commit and selected['pin']==pin and pin['version']=='6' and pin['production'] is True
    for name,h in selected['bundle_sha256'].items():assert sha256((BASE/'bundle'/name).read_bytes()).hexdigest()==h
    assert sha256((BASE/'bundle/portable.json').read_bytes()).hexdigest()==pin['portable_sha256']
    assert reference['lineage']['model_version']=='3' and reference['lineage']['partition']=='train'
    assert reference['lineage']['data_version']==pin['data_version'] and reference['lineage']['protocol_sha256']==pin['protocol_sha256']
    assert len(reference['samples'])==1024 and all(set(v)==set(portable['features']) for v in reference['samples'])
    proof=read('current/verification.json');p=read('current/plan.json');image=read('current/build.json')
    assert proof['passed'] is True and proof['commit']==commit and proof['model_pin']==p['model_pin']==pin
    assert proof['reference_lineage']==reference['lineage'] and proof['matching_events']==proof['successful_subscriber_requests']==9 and proof['unique_windows']==1
    assert image['tag']=='iot-coursework-drift-api:drift-event-jenkins-3' and image['config_digest']==image['cri_config_id']
    for n,h in image['packaged_sha256'].items():assert h==(selected['bundle_sha256'][n] if n in selected['bundle_sha256'] else source['files'][n])
    pods=read('current/receiver-pods.json')['items'];assert len(pods)==1 and [v['metadata']['uid'] for v in pods]==proof['pod_uids']
    status=next(v for v in pods[0]['status']['containerStatuses'] if v['name']=='kserve-container');assert status['ready'] and status['imageID'].split('://')[-1].split('@')[-1]==image['config_digest']
    for kind,name in [('brokers',p['broker']),('triggers',p['name']+'-trigger'),('inferenceservices',p['name'])]:
        obj=next(o for o in read('current/'+kind+'.json')['items'] if o['metadata']['name']==name)
        assert any(c['type']=='Ready' and c['status']=='True' for c in obj['status']['conditions'])
        if kind=='triggers':assert obj['spec']['filter']['attributes']['type']=='dev.edai.iot.drift.window' and obj['spec']['subscriber']['uri'].endswith('/v1/events/drift')
    installer=read('eventing-install/verification.json');declaration=json.loads((ROOT/'infra/eventing/assets.json').read_bytes())
    assert installer['passed'] is True and installer['version']==declaration['version']=='knative-v1.24.0' and installer['assets']==declaration['assets']
    for value in read('current/resolved-services.json').values():assert value['cluster_ip'].startswith('10.53.')
    published=read('current/publisher.json');assert len(published)==10 and len({v['event_id'] for v in published})==10 and all(v['status'] in [200,202,204] and v['response_utf8']=='' for v in published)
    assert published[0]['type']=='dev.edai.iot.ignored' and all(v['type']=='dev.edai.iot.drift.window' for v in published[1:])
    assert read('current/ready-before.json')['stored_windows']==read('current/ready-after-filter.json')['stored_windows']==0
    window=read('current/window.json');ledger=read('current/sqlite-readonly.json');assert len(ledger)==1 and ledger[0][1]==window['window_id'] and ledger[0][0]==window['reference_id']==sha256(canonical(reference)).hexdigest()
    utc=lambda s:datetime.fromisoformat(s.replace('Z','+00:00')).astimezone(timezone.utc).isoformat()
    normalized={**window,'start':utc(window['start']),'end':utc(window['end']),'observations':[]}
    for row in sorted(window['observations'],key=lambda v:v['id']):
        features={n:(int(v) if n in ['reading_count_10m','prior_failures_24h'] else float(v)) for n,v in row['features'].items()}
        normalized['observations'].append({**row,'event_timestamp':utc(row['event_timestamp']),'available_at':utc(row['available_at']),'features':features})
    assert sha256(canonical(normalized)).hexdigest()==ledger[0][2]
    assert read('current/direct-replay.json')=={'status':200,'response':json.loads(ledger[0][3])} and read('current/conflict.json')['status']==409
    assert counts((BASE/'current/metrics-after-delivery.prom').read_text(encoding='utf-8'))==9
    load=read('load/fixed1/verification.json');assert load['passed'] is True and load['api_source_commit']==commit
    loadsource=read('load/fixed1/source-manifest.json')
    for n,h in loadsource['files'].items():
        assert sha256(historical_blob(ROOT, loadsource['commit']+':'+n)).hexdigest()==h==sha256((BASE/'load/fixed1/source'/n).read_bytes()).hexdigest()
    requests=0
    for users in [1,10]:
        summary=read(f'load/fixed1/users-{users}-summary.json');text=(BASE/f'load/fixed1/users-{users}.html').read_text(encoding='utf-8')
        start=re.search(r'window\.templateArgs\s*=\s*',text);assert start;html=json.JSONDecoder().raw_decode(text[start.end():])[0]
        total=next(v for v in html['requests_statistics'] if v['name']=='Aggregated');assert summary['requests']==total['num_requests']>=100 and summary['failures']==total['num_failures']==0
        assert summary['p95_ack_ms']==total['response_time_percentile_0.95']<=500 and math.isclose(summary['requests_per_second'],total['total_rps'])
        assert summary['passed'] is True and not html['failures_statistics'] and not html['exceptions_statistics'];requests+=summary['requests']
        command=read(f'load/fixed1/users-{users}-command.json');assert command[command.index('--users')+1]==str(users) and command[command.index('--run-time')+1]=='60s' and command[command.index('--spawn-rate')+1]=='2'
        periodic=next(v for v in csv.DictReader(io.StringIO((BASE/f'load/fixed1/users-{users}_stats.csv').read_text(encoding='utf-8'))) if v['Name']=='Aggregated')
        assert 0<int(periodic['Request Count'])<=summary['requests'] and int(periodic['Failure Count'])==0
    before=counts((BASE/'load/fixed1/metrics-before.prom').read_text(encoding='utf-8'));after=counts((BASE/'load/fixed1/metrics-after.prom').read_text(encoding='utf-8'))
    assert after-before==requests==load['producer_requests'] and load['subscriber_204_before']==before and load['subscriber_204_after']==after
    assert read('load/fixed1/sqlite-before.json')==read('load/fixed1/sqlite-after.json')==ledger
    assert identities(read('load/fixed1/pods-before.json'))==identities(read('load/fixed1/pods-after.json'))==load['pod_uids_unchanged']
    controls={phase:read('load/fixed1-control/hpas-'+phase+'.json') for phase in ['before','fixed','restored']}
    for name in controls['before']:
        values=[controls[s][name] for s in controls];assert len({v['metadata']['uid'] for v in values})==1 and all(v['spec']['minReplicas']==1 for v in values)
        assert controls['before'][name]['spec']['maxReplicas']==controls['restored'][name]['spec']['maxReplicas']==10 and controls['fixed'][name]['spec']['maxReplicas']==1
    assert identities(read('load/pods-before.json'))!=identities(read('load/pods-after.json'))
    assert 'ExecMainStatus=1' in (BASE/'units/iot-eventing-load-28c.txt').read_text(encoding='utf-8') and 'ExecMainStatus=0' in (BASE/'units/iot-eventing-load-fixed1-28c.txt').read_text(encoding='utf-8')
    assert read('pilot-failed/resume/publisher.json')[1]['status']==500 and 'non-empty response not recognized as CloudEvent' in (BASE/'pilot-failed/mt-broker-filter-logs.txt').read_text(encoding='utf-8')
    assert all((BASE/'screenshots'/n).read_bytes().startswith(b'\xff\xd8\xff') for n in ['jenkins-eventing3-success.jpg','jenkins-eventing3-stages.jpg','locust-eventing-1-user.jpg','locust-eventing-10-users.jpg'])
    print(f'PASS {len(actual)}payload;106Git inputs/source-after;Jenkins3SUCCESS/8stages;101main+20subtests;65statements10branches100%;103/119mutants86.55%;production6/reference3exactdata+schema;9successfuldeliveries/1window/replay409;{requests}pacedBrokerACKs match subscriber204delta;stablePods/HPAsrestored1..10;originalfailures/HTML/CSV/4JPEG retained.')


if __name__=='__main__':main()
