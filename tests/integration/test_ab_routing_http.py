"""Exercise actual loopback HTTP through two predictors; only registry selection and Feast reads are fixtures."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack,contextmanager
from hashlib import sha256
from pathlib import Path
import json,os,socket,threading,time
import httpx,uvicorn
from prometheus_client.parser import text_string_to_metric_families
from ml.serving_ci import ab_gate
from services.ab_api.app import create_app
from services.feature_api.runtime import FeastKServeBackend
from services.predictor.app import create_app as predictor_app
from tests.api.test_ab_api import fixture_runtime


@contextmanager
def listening(app):
    """Bind an ephemeral loopback-only port first, run a real ASGI server and join every thread on exit."""
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False,workers=1))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+15
        while not server.started:
            if not thread.is_alive() or time.monotonic()>deadline:raise RuntimeError('Loopback ASGI startup failed')
            time.sleep(.01)
        yield 'http://127.0.0.1:'+str(sock.getsockname()[1])
    finally:
        server.should_exit=True;thread.join(10);sock.close()
        assert not thread.is_alive(),'Preserve a live server handle for investigation; never claim cleanup if still running'


def predictor_count(client,url):
    """Read the predictor's actual HTTP middleware counter independently of the A/B SQLite ledger."""
    raw=client.get(url+'/metrics');raw.raise_for_status()
    values=[sample.value for family in text_string_to_metric_families(raw.text) for sample in family.samples
        if sample.name=='iot_predictor_requests_total' and sample.labels=={'outcome':'success'}]
    return sum(values),raw.text


def test_actual_http_split_persistence_monitoring_and_replay(tmp_path):
    """Verify400unique trials,40HTTP retries and independent model counters, retaining truthful optional PC proof."""
    runtime=fixture_runtime(tmp_path)
    root=Path(__file__).resolve().parents[2]
    with ExitStack() as stack,httpx.Client(timeout=20,trust_env=False) as client:
        urls={'baseline':stack.enter_context(listening(predictor_app(root/'models/pump-failure-v1'))),
            'candidate':stack.enter_context(listening(predictor_app(tmp_path/'candidate')))}
        for variant,feature_runtime in runtime.runtimes.items():
            backend=feature_runtime.backend;backend.endpoint=urls[variant]
            # Keep the real native adapter's HTTP method; only online feature fetch remains controlled.
            backend.predict=FeastKServeBackend.predict.__get__(backend)
        url=stack.enter_context(listening(create_app(runtime)))
        ready=client.get(url+'/health/ready');ready.raise_for_status();before={v:predictor_count(client,u)[0] for v,u in urls.items()}
        responses=[];raw_replies=[];elapsed=[]
        for cycle in range(2):
            for machine in runtime.context['machines']:
                started=time.perf_counter();response=client.post(url+'/v1/machines/'+machine+':predict',json={},headers={'X-Request-ID':f'http-{cycle}-{machine}'})
                elapsed.append(1000*(time.perf_counter()-started));response.raise_for_status();responses.append(response.json());raw_replies.append(response.content)
        def replay(index):
            """Use separate HTTP clients so simultaneous retries exercise real server concurrency and database locks."""
            original=responses[index]
            with httpx.Client(timeout=20,trust_env=False) as worker:
                reply=worker.post(url+'/v1/machines/'+original['machine_id']+':predict',json={},headers={'X-Request-ID':original['request_id']})
                reply.raise_for_status();assert reply.json()==original and reply.content==raw_replies[index]
        with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(replay,range(40)))
        snapshot=client.get(url+'/v1/experiment');snapshot.raise_for_status();data=snapshot.json()
        metrics=client.get(url+'/metrics');metrics.raise_for_status()
        model_counters={};model_metrics={}
        for variant,target in urls.items():
            count,raw=predictor_count(client,target);model_counters[variant]=count-before[variant];model_metrics[variant]=raw
            assert count-before[variant]==data['metrics'][variant]['requests']
        assert len(data['observations'])==400 and sum(d['unique_rows'] for d in data['drift'].values())==200
        assert sum(m['failures'] for m in data['metrics'].values())==0
        report={**data,'passed':True,'staged_isvc_uid':'pc-fixture-no-kubernetes'}
        counts=ab_gate(report,runtime.pins['candidate'],'pc-fixture-no-kubernetes')
        # This validates gate shape using a declared fixture UID; it is not native deployment authorization.
        failures={}
        for name,path,body,identity in [('conflict','P-002',{},responses[0]['request_id']),
                ('unknown','P-999',{},'unknown'),('label','P-001',{'failure_in_next_1h':1},'label'),
                ('override','P-001',{'variant':'candidate'},'override')]:
            reply=client.post(url+'/v1/machines/'+path+':predict',json=body,headers={'X-Request-ID':identity});failures[name]=reply.status_code
        assert failures=={'conflict':409,'unknown':404,'label':422,'override':422}
        assert client.get(url+'/v1/experiment').json()==data
        # A new worker uses the existing durable ledger and reads exactly the same report.
        other=fixture_runtime(tmp_path);assert other.snapshot()==data
        proof={'passed':True,'machine_scope':'PC Windows, three actual loopback HTTP ASGI servers; same local Python dependencies',
            'unique_http_predictions':400,'concurrent_http_retries':40,'replay_added_trials':0,'model_counter_deltas':model_counters,
            'cohort_requests':counts,'unique_feature_snapshots':{v:data['drift'][v]['unique_rows'] for v in counts},
            'validation_statuses':failures,'mean_client_elapsed_ms':sum(elapsed)/len(elapsed),'max_client_elapsed_ms':max(elapsed),
            'latency_scope':'Measured PC client end-to-end durations; not Locust, native load, SLA or model-speed comparison.',
            'registry_and_feast_are_fixtures':True,'both_variants_use_legacy_portable_weights':True,
            'real_registry_changed':False,'native_kubernetes_deployed':False,'staged_isvc_uid_is_fixture':True,
            'scope':'Genuine HTTP routing, pinned model arithmetic, persistent replay and label-free monitoring on controlled features. Not real registry promotion, two newly trained models, native CI or statistical/model-quality evidence.'}
        destination=os.environ.get('IOT_AB_PROOF_DIR')
        if destination:
            output=Path(destination);output.mkdir(parents=True,exist_ok=False)
            for name,value in [('verification.json',proof),('readback.json',data),('gate-contract-fixture.json',report)]:
                raw=json.dumps(value,separators=(',',':'),allow_nan=False).encode()+b'\n';assert len(raw)<1_000_000;(output/name).write_bytes(raw)
            (output/'metrics.txt').write_text(metrics.text,encoding='utf-8')
            for name,raw in model_metrics.items():(output/(name+'-predictor-metrics.txt')).write_text(raw,encoding='utf-8')
            files={p.name:{'sha256':sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in output.iterdir()}
            (output/'manifest.json').write_text(json.dumps({'files':files,'scope':proof['scope']},indent=2)+'\n',encoding='utf-8')
