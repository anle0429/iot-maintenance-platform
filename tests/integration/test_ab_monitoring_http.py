"""Run real standalone Prometheus and Grafana against three loopback HTTP APIs; no Docker or cloud required."""
from contextlib import ExitStack,contextmanager
from hashlib import sha256
from pathlib import Path
import importlib.util,json,math,os,secrets,socket,subprocess,time
import httpx,pytest
from ml import ab_ci
from ml.ab_monitoring import DATASOURCE_UID,DASHBOARD_UID,panel_queries,write_bundle
from services.ab_api.app import create_app
from services.feature_api.runtime import FeastKServeBackend
from services.predictor.app import create_app as predictor_app
from tests.api.test_ab_api import fixture_runtime,ASOF
from tests.integration.test_ab_routing_http import listening

ROOT=Path(__file__).resolve().parents[2]
pytestmark=pytest.mark.skipif(not all(os.environ.get(k) for k in ['IOT_PROMETHEUS_EXE','IOT_GRAFANA_EXE','IOT_GRAFANA_HOME']),reason='Explicit standalone Prometheus/Grafana binaries required; no simulated dashboard')


def unused_port():
    """Choose a loopback port; binding races fail at startup rather than exposing another interface."""
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def eventually(check,seconds=30):
    """Bound service/query readiness; preserve the last error rather than accepting missing data."""
    deadline=time.monotonic()+seconds;last=None
    while time.monotonic()<deadline:
        try:
            result=check()
            if result:return result
        except (httpx.HTTPError,AssertionError,KeyError,IndexError,ValueError) as error:last=error
        time.sleep(.2)
    raise AssertionError('Service or query did not become ready') from last


@contextmanager
def child(command,log,env):
    """Capture logs and terminate only this owned process, then wait for actual exit on every path."""
    with Path(log).open('wb') as console:
        process=subprocess.Popen(command,stdout=console,stderr=subprocess.STDOUT,env=env,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:yield process
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(15)
                except subprocess.TimeoutExpired:process.kill();process.wait(10)
            assert process.poll() is not None


def quantile_from_durations(durations):
    """Independently interpolate P95 over the published finite histogram bounds, not an exact sample quantile."""
    rank=.95*len(durations);previous_bound=0;previous_count=0
    for bound in [.005,.01,.025,.05,.1,.25,.5,1,5]:
        count=sum(value<=bound for value in durations)
        if count>=rank:return previous_bound+(bound-previous_bound)*(rank-previous_count)/(count-previous_count)
        previous_bound,previous_count=bound,count
    return 5.0


def expected_panels(data):
    """Derive counts, snapshots, mean scores and binned P95 from raw ledger rows; PSI comes from the checked report."""
    expected={i:{} for i in range(1,7)}
    for variant in ['baseline','candidate']:
        rows=[r for r in data['observations'] if r['variant']==variant];version=data[variant+'_pin']['version'];key=(variant,version,'')
        expected[1][key]=len(rows);expected[2][key]=sum(r['status']!=200 for r in rows)
        expected[3][key]=len({(r['machine_id'],r['feature_trace']['feature_event_ts']) for r in rows})
        expected[4][key]=quantile_from_durations([r['latency_ms']/1000 for r in rows])
        scores=[r['prediction']['risk_score'] for r in rows if r['status']==200];expected[5][key]=sum(scores)/len(scores)
        for feature,value in data['drift'][variant]['features'].items():expected[6][(variant,version,feature)]=value['psi']
    return expected


def compare_samples(samples,expected,experiment,require_experiment=True):
    """Reject missing/duplicate/nonfinite series; aggregation drops experiment only in the P95 expression."""
    actual={}
    for labels,value in samples:
        if require_experiment:assert labels['experiment']==experiment
        else:assert 'experiment' not in labels
        key=(labels['variant'],labels['version'],labels.get('feature',''))
        assert key not in actual and math.isfinite(value);actual[key]=value
    assert actual.keys()==expected.keys()
    assert all(math.isclose(actual[k],value,rel_tol=1e-10,abs_tol=1e-12) for k,value in expected.items())


def test_real_prometheus_and_grafana_panels(tmp_path):
    """Verify800HTTP trials/40replays, six PromQL/Grafana panels and loss/recovery of the actual scrape target."""
    root=tmp_path/'monitoring';root.mkdir();runtime=fixture_runtime(root)
    spec=importlib.util.spec_from_file_location('monitoring_ci_driver',ROOT/'scripts/ci-serving.py');driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
    offline={m:{'features':runtime.runtimes['baseline'].backend.fetch(m)['values'],'feature_event_ts':ASOF.isoformat()} for m in runtime.context['machines']}
    prometheus_port,grafana_port=unused_port(),unused_port()
    while prometheus_port==grafana_port:grafana_port=unused_port()
    with ExitStack() as stack,httpx.Client(timeout=15,trust_env=False) as client:
        urls={v:stack.enter_context(listening(predictor_app(ROOT/'models/pump-failure-v1' if v=='baseline' else root/'candidate'))) for v in ['baseline','candidate']}
        for v,feature in runtime.runtimes.items():
            feature.backend.endpoint=urls[v];feature.backend.predict=FeastKServeBackend.predict.__get__(feature.backend)
        url=stack.enter_context(listening(create_app(runtime)))
        launch=write_bundle(root/'runtime',url,runtime.experiment,'PC fixture features - same model weights',prometheus_port,grafana_port)
        env={**os.environ,'GOMEMLIMIT':'384MiB','GOMAXPROCS':'2','GF_SECURITY_ADMIN_USER':'local-audit',
            'GF_SECURITY_ADMIN_PASSWORD':secrets.token_urlsafe(32)}
        executable=Path(os.environ['IOT_PROMETHEUS_EXE']);promtool=executable.with_name('promtool.exe' if executable.suffix=='.exe' else 'promtool')
        check=subprocess.run([str(promtool),'check','config',str(root/'runtime/prometheus.json')],capture_output=True,text=True,timeout=20)
        (root/'promtool.txt').write_text(check.stdout+check.stderr,encoding='utf-8');assert check.returncode==0
        prom=stack.enter_context(child([os.environ['IOT_PROMETHEUS_EXE'],*launch['prometheus_arguments']],root/'prometheus.log',env))
        graf=stack.enter_context(child([os.environ['IOT_GRAFANA_EXE'],*launch['grafana_arguments'],'--homepath='+os.environ['IOT_GRAFANA_HOME']],root/'grafana.log',env))
        purl,gurl=launch['prometheus_url'],launch['grafana_url']
        eventually(lambda:client.get(purl+'/-/ready').status_code==200)
        health=eventually(lambda: (r.json() if (r:=client.get(gurl+'/api/health')).status_code==200 else None),60)
        assert health['database']=='ok'
        def query(expr):
            """Ask the real Prometheus query engine, not a text parser or a PromQL imitation."""
            response=client.get(purl+'/api/v1/query',params={'query':expr});response.raise_for_status();body=response.json()
            assert body['status']=='success' and body['data']['resultType']=='vector';return body
        eventually(lambda:query('up{job="iot-ab"}')['data']['result'][0]['value'][1]=='1')
        probe=ab_ci.probe(lambda path,body=None,headers=None:driver.http(url,path,body,headers),lambda path:driver.http_text(url,path),
            lambda v:driver.http_text(urls[v],'/metrics'),runtime.context,runtime.experiment,runtime.pins,runtime.detector.reference_id,offline)
        data=probe['readback'];expected=expected_panels(data)
        eventually(lambda:sum(float(x['value'][1]) for x in query('iot_ab_requests_total')['data']['result'])==800)
        dashboard=eventually(lambda: (r.json() if (r:=client.get(gurl+'/api/dashboards/uid/'+DASHBOARD_UID)).status_code==200 else None))
        source=json.loads((root/'runtime/dashboards/ab.json').read_text(encoding='utf-8'));assert panel_queries(dashboard['dashboard'],runtime.experiment)==panel_queries(source,runtime.experiment)
        datasource=client.get(gurl+'/api/datasources/uid/'+DATASOURCE_UID);datasource.raise_for_status();assert datasource.json()['url']==purl
        results=[]
        for panel in panel_queries(source,runtime.experiment):
            promql=query(panel['expr']);samples=[(r['metric'],float(r['value'][1])) for r in promql['data']['result']]
            compare_samples(samples,expected[panel['panel']],runtime.experiment,panel['panel']!=4)
            response=client.post(gurl+'/api/ds/query',json={'from':str(int((time.time()-300)*1000)),'to':str(int(time.time()*1000)),
                'queries':[{'refId':'A','datasource':{'uid':DATASOURCE_UID,'type':'prometheus'},'expr':panel['expr'],
                'instant':True,'range':False,'format':'time_series','intervalMs':2000,'maxDataPoints':100}]})
            response.raise_for_status();grafana=response.json();answer=grafana['results']['A'];assert answer.get('status',200)==200 and not answer.get('error')
            plotted=[]
            for frame in answer['frames']:
                for i,field in enumerate(frame['schema']['fields']):
                    if field['type']=='number':
                        values=frame['data']['values'][i];assert len(values)==1;plotted.append((field['labels'],values[0]))
            compare_samples(plotted,expected[panel['panel']],runtime.experiment,panel['panel']!=4)
            results.append({**panel,'prometheus':promql,'grafana':grafana,'series_count':len(samples),'matches_ledger':True})
        empty=query('iot_ab_requests_total{experiment="missing-experiment"}');assert empty['data']['result']==[]
        invalid=client.get(purl+'/api/v1/query',params={'query':'not valid promql !!'});assert invalid.status_code==400
        target_path=root/'runtime/targets/ab.json';original=target_path.read_bytes();dead=unused_port()
        replacement=target_path.with_suffix('.tmp');replacement.write_text(json.dumps([{'targets':['127.0.0.1:'+str(dead)]}]),encoding='utf-8');replacement.replace(target_path)
        eventually(lambda:query('up{job="iot-ab"}')['data']['result'][0]['value'][1]=='0')
        down=client.get(purl+'/api/v1/targets').json();assert down['data']['activeTargets'][0]['health']=='down'
        replacement.write_bytes(original);replacement.replace(target_path)
        eventually(lambda:query('up{job="iot-ab"}')['data']['result'][0]['value'][1]=='1')
        assert client.get(url+'/v1/experiment').json()==data
        targets=client.get(purl+'/api/v1/targets').json();assert targets['data']['activeTargets'][0]['health']=='up'
        version=client.get(purl+'/api/v1/status/buildinfo').json()
        proof={'passed':True,'actual_http_trials':800,'concurrent_replays':40,'replay_added_trials':0,'cohort_counts':probe['cohort_counts'],
            'unique_snapshots':{v:data['drift'][v]['unique_rows'] for v in ['baseline','candidate']},'panels_checked':6,
            'prometheus_version':version['data']['version'],'grafana_version':health['version'],'datasource_uid':DATASOURCE_UID,
            'dashboard_uid':DASHBOARD_UID,'empty_experiment_has_no_series':True,'invalid_promql_status':400,
            'actual_scrape_down_then_recovered':True,'label_free':True,'registry_and_feast_are_fixtures':True,
            'both_versions_use_same_legacy_weights':True,'cloud_or_kubernetes_contacted':False,
            'scope':'Real Prometheus scrape/query and Grafana file provisioning/datasource query on Windows PC, against three real loopback HTTP APIs. Controlled feature/registry fixtures, not VM deployment, model quality, production monitoring or final service authentication.'}
        for name,value in [('verification.json',proof),('panel-queries.json',results),('grafana-dashboard-readback.json',dashboard),
                ('prometheus-targets.json',targets),('scrape-down.json',down),('prometheus-build.json',version),('grafana-health.json',health)]:
            (root/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        # Retain a compact independent value table rather than publishing800 repeated full trace dictionaries.
        values={str(panel):[{'variant':k[0],'version':k[1],'feature':k[2],'value':v} for k,v in rows.items()] for panel,rows in expected.items()}
        (root/'ledger-expected-values.json').write_text(json.dumps(values,indent=2)+'\n',encoding='utf-8')
        (root/'ab-metrics.txt').write_bytes(probe['metrics'].encode())
        (root/'ledger-private.json').write_bytes(ab_ci.canonical(data)+b'\n')
        hold=os.environ.get('IOT_MONITORING_HOLD_FILE')
        if hold:
            (root/'ui-ready.json').write_text(json.dumps({'dashboard_url':gurl+'/d/'+DASHBOARD_UID+'?orgId=1&var-experiment='+runtime.experiment+'&from=now-5m&to=now','prometheus_url':purl,'pids':[prom.pid,graf.pid]}),encoding='utf-8')
            deadline=time.monotonic()+600
            while not Path(hold).exists() and time.monotonic()<deadline:
                assert prom.poll() is None and graf.poll() is None;time.sleep(.5)
    (root/'owned-processes-stopped.json').write_text(json.dumps({'prometheus_exit_code':prom.returncode,'grafana_exit_code':graf.returncode,
        'all_api_threads_joined':True}),encoding='utf-8')
