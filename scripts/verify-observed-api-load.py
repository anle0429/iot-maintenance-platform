"""Audit the real instrumented-API Locust reports, fresh model6 features, fixed replica identity and preserved failed guard offline."""
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import csv,importlib.util,io,json,math,re,subprocess
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'docs/evidence/observed-api-load-linux'


def read(name):
    """Decode retained JSON only after the original-byte manifests are checked."""
    return json.loads((BASE/name).read_bytes())


def main():
    """Recalculate report/SLA facts and bind runtime source, image, lease and replica scope."""
    seal=read('manifest.json')
    assert set(seal['files'])=={p.relative_to(BASE).as_posix() for p in BASE.rglob('*') if p.is_file() and p!=BASE/'manifest.json'}
    for name,v in seal['files'].items():
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        raw=(BASE/name).read_bytes();assert len(raw)==v['bytes'] and sha256(raw).hexdigest()==v['sha256'],name
    native=read('native-manifest.json')
    omitted=set(seal['omitted_native_bookkeeping'])
    assert omitted=={folder+'/source/tests/load/__pycache__/feature_api.cpython-312.pyc' for folder in ['locust','locust-fixed1']}
    assert len(native['files'])==74 and all(v==seal['files'][n] for n,v in native['files'].items() if n not in omitted)
    assert set(native['files'])-omitted<=seal['files'].keys() and len(seal['files'])==75
    source=read('locust-fixed1/source-manifest.json')
    assert set(source['files'])=={'tests/load/feature_api.py','tests/load/requirements.txt'}
    for name,wanted in source['files'].items():
        raw=subprocess.check_output(['git','show',source['commit']+':'+name],cwd=ROOT)
        assert sha256(raw).hexdigest()==wanted==sha256((BASE/'locust-fixed1/source'/name).read_bytes()).hexdigest()
    assert 'locust==2.47.0' in (BASE/'locust-fixed1/requirements-installed.lock').read_text().lower()
    for users,requests,p95 in [(1,598,6),(10,5767,28)]:
        summary=read(f'locust-fixed1/users-{users}-summary.json')
        text=(BASE/f'locust-fixed1/users-{users}.html').read_text(encoding='utf-8')
        start=re.search(r'window\.templateArgs\s*=\s*',text);assert start
        html=json.JSONDecoder().raw_decode(text[start.end():])[0]
        aggregate=next(r for r in html['requests_statistics'] if r['name']=='Aggregated')
        assert summary['requests']==aggregate['num_requests']==requests and summary['failures']==aggregate['num_failures']==0
        assert summary['p95_ms']==aggregate['response_time_percentile_0.95']==p95
        assert math.isclose(summary['requests_per_second'],aggregate['total_rps'])
        assert not html['failures_statistics'] and not html['exceptions_statistics']
        sla=summary['sla'];assert sla=={'minimum_requests':100,'maximum_failures':0,'p95_ms':500,'clock_mode':'simulation'}
        assert summary['passed'] is True and requests>=sla['minimum_requests'] and p95<=sla['p95_ms']
        command=read(f'locust-fixed1/users-{users}-command.json')
        assert command[command.index('--users')+1]==str(users) and command[command.index('--run-time')+1]=='60s' and command[command.index('--spawn-rate')+1]=='2'
        stats=list(csv.DictReader(io.StringIO((BASE/f'locust-fixed1/users-{users}_stats.csv').read_text())))
        periodic=next(r for r in stats if r['Name']=='Aggregated')
        assert 0<int(periodic['Request Count'])<=requests and int(periodic['Failure Count'])==0
        history=list(csv.DictReader(io.StringIO((BASE/f'locust-fixed1/users-{users}_stats_history.csv').read_text())))
        assert int(history[-1]['Total Request Count'])==int(periodic['Request Count'])
        assert int(history[-1]['Timestamp'])<=datetime.fromisoformat(html['end_time'].replace('Z','+00:00')).timestamp()
    control={k:read('fixed1-control/hpa-'+k+'.json') for k in ['before','fixed']}
    control['restored']=read('locust-fixed1/hpa-restored.json')
    assert len({v['metadata']['uid'] for v in control.values()})==1
    assert control['before']['spec']['minReplicas']==control['restored']['spec']['minReplicas']==control['fixed']['spec']['minReplicas']==1
    assert control['before']['spec']['maxReplicas']==control['restored']['spec']['maxReplicas']==2 and control['fixed']['spec']['maxReplicas']==1
    identities=[]
    build=json.loads((ROOT/'docs/evidence/api-observability-linux/native/image-build/verification.json').read_bytes())
    for stage in ['before','after']:
        value=read('locust-fixed1/'+stage+'.json');pods=[p for p in value['pods']['items'] if not p['metadata'].get('deletionTimestamp')]
        assert len(pods)==value['deployment']['spec']['replicas']==1
        for pod in pods:
            container=next(c for c in pod['status']['containerStatuses'] if c['name']=='api')
            assert container['ready'] and container['imageID']==build['images']['feature']['config_digest']
        identities.append({p['metadata']['uid'] for p in pods})
    assert identities[0]==identities[1]
    old_before=read('locust/before.json');old_after=read('locust/after.json')
    assert {p['metadata']['uid'] for p in old_before['pods']['items']}!={p['metadata']['uid'] for p in old_after['pods']['items']}
    assert 'ExecMainStatus=1' in (BASE/'load-unit.txt').read_text() and 'AssertionError' in (BASE/'load-journal.txt').read_text()
    assert 'ExecMainStatus=0' in (BASE/'load-fixed-unit.txt').read_text() and 'Result=success' in (BASE/'load-fixed-unit.txt').read_text()
    assert read('schedule-restored-v2.json')['after_paused'] is False
    state=read('state/context.json');prepared=datetime.fromisoformat(state['prepared_wall_utc'])
    checked=datetime.fromisoformat(read('locust-fixed1/verification.json')['checked_utc'])
    assert 0<(checked-prepared).total_seconds()<3600
    assert read('baseline-refresh/physical-ttl-before.json')['keys']==0
    ttl=read('baseline-refresh/physical-ttl-after.json');assert ttl['keys']==len(ttl['pttl_ms'])==200 and min(ttl['pttl_ms'])>3300000
    spec=importlib.util.spec_from_file_location('serving_load_oracle',ROOT/'scripts/verify-native-serving.py')
    oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
    reference=ROOT/'docs/evidence/ci-serving-linux/selected-model6'
    oracle.all_machine_rows(read('baseline-refresh/http-all-machines.json'),json.loads((reference/'portable.json').read_bytes()),json.loads((reference/'model-pin.json').read_bytes()))
    assert all((BASE/'screenshots'/n).read_bytes().startswith(b'\xff\xd8\xff') for n in ['locust-1-user.jpg','locust-10-users.jpg'])
    print('PASS75payload/2originalJPEG;598+5767requests/0failures/P95 6+28ms; HTML+periodicCSV; native model6/200predictions/freshTTL; fixed1samePod and restoredHPA1..2; original changed-Pod guard failure retained. Two bytecode caches omitted explicitly; GETfeatures only, not maximum capacity or predictionTPS.')


if __name__=='__main__':main()
