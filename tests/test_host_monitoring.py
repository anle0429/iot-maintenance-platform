"""Protect private host configuration and verify counter-rate edge cases with hand-computed answers."""
from pathlib import Path
import json,re,subprocess,os
import pytest
from ml.host_monitoring import dashboard,exporter_arguments,profile,queries,write_bundle,DATASOURCE_UID
from tests.monitoring_oracle import counter_rate


@pytest.mark.parametrize('family',['windows','linux'])
def test_explicit_profiles_and_private_provisioning(tmp_path,family):
    """Every panel has units and a concrete datasource; PII-bearing metrics are excluded before Prometheus ingestion."""
    p=profile(family);doc=dashboard(family,'pc-test','PC integration test')
    assert len(doc['panels'])==9 and len(queries(family,'pc-test'))==11
    assert all(x['datasource']['uid']==DATASOURCE_UID for x in doc['panels'])
    assert all('$host' not in x['expr'] for x in queries(family,'pc-test'))
    assert 'vector(0)' not in json.dumps(doc) and 'or 0' not in json.dumps(doc)
    args=exporter_arguments(family,19282);assert args[0]=='--web.listen-address=127.0.0.1:19282'
    root=tmp_path/'monitor';launch=write_bundle(root,'http://127.0.0.1:19282',family,'pc-test','PC integration test')
    config=json.loads((root/'prometheus.json').read_text(encoding='utf-8'));keep=config['scrape_configs'][0]['metric_relabel_configs'][0]
    for name in ['windows_net_nic_info','windows_net_nic_address_info','node_uname_info','windows_process_info']:
        assert not re.fullmatch(keep['regex'],name)
    assert all(re.fullmatch(keep['regex'],name) for name in p['metric_names']) and keep['action']=='keep'
    assert '--web.listen-address=127.0.0.1:19091' in launch['prometheus_arguments']
    assert 'http_addr = 127.0.0.1' in (root/'grafana.ini').read_text(encoding='utf-8')
    before=(root/'dashboards/host.json').read_bytes()
    with pytest.raises(FileExistsError):write_bundle(root,'http://127.0.0.1:19282',family,'pc-test','PC integration test')
    assert (root/'dashboards/host.json').read_bytes()==before


@pytest.mark.parametrize('kwargs',[{'family':'automatic'},{'host':'my"host'},{'label':'bad\nlabel'},{'endpoint':'http://8.8.8.8'},
    {'prometheus_port':80},{'grafana_port':19091},{'grafana_port':True}])
def test_invalid_input_preserves_filesystem(tmp_path,kwargs):
    """Invalid scope, endpoint or binding cannot leave a partially initialized runtime."""
    values={'destination':tmp_path/'runtime','endpoint':'http://127.0.0.1:19282','family':'windows','host':'pc-test','label':'PC test',**kwargs}
    with pytest.raises(ValueError):write_bundle(**values)
    assert not values['destination'].exists()


@pytest.mark.parametrize('samples,end,window,expected',[
    ([(10,110),(20,5),(30,15),(40,25)],40,40,5/6),
    ([(0,0),(10,10),(20,20)],20,30,2/3),
    ([(0,100),(10,110)],40,50,.5),
    ([(10,100),(20,100),(30,100)],30,30,0),
    ([(10,100),(20,110),(30,120)],30,30,1)])
def test_counter_reset_start_and_missing_boundaries(samples,end,window,expected):
    """Counters need reset compensation and bounded extrapolation, not simply last minus first divided by30."""
    assert counter_rate(samples,end,window)==pytest.approx(expected,abs=1e-12)


@pytest.mark.parametrize('samples',[[[10,1]],[[10,1],[10,2]],[[10,2],[9,3]],[[10,1],[20,float('nan')]],[[0,1],[20,2]],[[10,-1],[20,2]]])
def test_unusable_counter_windows_are_not_zero(samples):
    """Missing or corrupt samples must fail the evidence check instead of inventing a zero rate."""
    with pytest.raises(ValueError):counter_rate(samples,30,30)


def test_known_answer_promql_in_actual_promtool():
    """Evaluate both OS profiles plus reset/start/gap fixtures in the real Prometheus rule-test engine."""
    executable=os.environ.get('IOT_PROMETHEUS_EXE')
    if not executable:pytest.skip('Explicit promtool binary required; fixtures are not a real host exporter')
    binary=Path(executable).with_name('promtool.exe' if Path(executable).suffix=='.exe' else 'promtool')
    fixture=Path(__file__).resolve().parent/'fixtures/host-monitoring-promql.json'
    cases=json.loads(fixture.read_text(encoding='utf-8'))['tests']
    for family,case in zip(['windows','linux'],cases[:2],strict=True):
        assert [x['expr'] for x in case['promql_expr_test']]==[x['expr'] for x in queries(family,'pc-test')]
    for family,case in zip(['windows','linux'],cases[3:],strict=True):
        expressions=[x['expr'] for x in queries(family,'pc-test')]
        assert [x['expr'] for x in case['promql_expr_test']]==[expressions[i] for i in [0,5,6,7,8]]
        assert all(x['exp_samples']==[] for x in case['promql_expr_test'])
    result=subprocess.run([str(binary),'test','rules',str(fixture)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
