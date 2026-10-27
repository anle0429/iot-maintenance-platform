"""Protect datasource resolution, private bindings and fresh-only runtime configuration."""
import json
from pathlib import Path
import pytest
from ml.ab_monitoring import DATASOURCE_UID,dashboard,panel_queries,private_target,write_bundle

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('endpoint',['https://127.0.0.1:1234','http://example.com','http://8.8.8.8','http://169.254.169.254','http://0.0.0.0','http://[::1]:1234','http://127.0.0.2:1234','http://u:p@10.52.0.2','http://127.0.0.1/x','http://127.0.0.1?secret=x','http://127.0.0.1#x','http://10.52.1.2:99999','http://127.0.0.1:0'])
def test_reject_nonprivate_or_ambiguous_endpoints(endpoint):
    """No public service, metadata endpoint, credential-bearing URL or resolver-dependent host is accepted."""
    with pytest.raises(ValueError):private_target(endpoint)


@pytest.mark.parametrize('endpoint,expected',[('http://127.0.0.1:18001','127.0.0.1:18001'),('http://10.52.0.2','10.52.0.2:80'),('http://172.20.0.2:8000','172.20.0.2:8000'),('http://192.168.1.2:8000','192.168.1.2:8000')])
def test_explicit_internal_targets(endpoint,expected):
    """Both real loopback verification and observed private VM ClusterIP addresses are supported."""
    assert private_target(endpoint)==expected


def test_provisioned_dashboard_and_fresh_state(tmp_path):
    """Grafana gets concrete datasource UIDs in every panel and experiment variable without editing the template."""
    template=json.loads((ROOT/'infra/ab-api/grafana-dashboard.json').read_text(encoding='utf-8'));before=json.dumps(template)
    root=tmp_path/'runtime';launch=write_bundle(root,'http://127.0.0.1:18001','serve-jenkins-7','PC fixture features',template=template)
    rendered=json.loads((root/'dashboards/ab.json').read_text(encoding='utf-8'))
    assert '__inputs' not in rendered and '${DS_PROMETHEUS}' not in json.dumps(rendered)
    assert all(p['datasource']['uid']==DATASOURCE_UID for p in rendered['panels'])
    assert rendered['templating']['list'][0]['datasource']['uid']==DATASOURCE_UID
    assert rendered['templating']['list'][0]['current']['value']=='serve-jenkins-7'
    assert json.dumps(template)==before and len(panel_queries(rendered,'other-exp'))==6
    assert all('$experiment' not in q['expr'] and 'other-exp' in q['expr'] for q in panel_queries(rendered,'other-exp'))
    ini=(root/'grafana.ini').read_text(encoding='utf-8');assert 'http_addr = 127.0.0.1' in ini and 'org_role = Viewer' in ini and 'PASSWORD' not in ini
    assert '--web.listen-address=127.0.0.1:19090' in launch['prometheus_arguments']
    assert not (root/'grafana-data/grafana.db').exists()
    with pytest.raises(FileExistsError):write_bundle(root,'http://127.0.0.1:18001','serve-jenkins-7','PC fixture features')
    assert (root/'dashboards/ab.json').read_text(encoding='utf-8')==json.dumps(rendered,indent=2)+'\n'


@pytest.mark.parametrize('kwargs',[{'experiment':'x"}'},{'label':'bad\nlabel'},{'prometheus_port':80},{'grafana_port':19090},{'grafana_port':True}])
def test_invalid_configuration_writes_nothing(tmp_path,kwargs):
    """Malformed labels or ports fail before creating any runtime directory."""
    args={'destination':tmp_path/'runtime','endpoint':'http://127.0.0.1:18001','experiment':'trial-1','label':'PC fixture',**kwargs}
    with pytest.raises(ValueError):write_bundle(**args)
    assert not args['destination'].exists()


def test_wrong_dashboard_rejected():
    """A different dashboard or unresolved input cannot silently pass as the six tested queries."""
    with pytest.raises(ValueError):dashboard({'uid':'other','panels':[]},'trial-1','PC fixture')
    template=json.loads((ROOT/'infra/ab-api/grafana-dashboard.json').read_text(encoding='utf-8'));template['description']='${DS_OTHER}'
    with pytest.raises(ValueError):dashboard(template,'trial-1','PC fixture')
