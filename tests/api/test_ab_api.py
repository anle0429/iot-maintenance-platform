"""Verify actual model arithmetic, persistent concurrent replay, drift oracles and the two-model HTTP boundary."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio,json,math,sqlite3,threading
from unittest.mock import patch
import httpx,pytest
from fastapi.testclient import TestClient
from hypothesis import given,settings,strategies as st
from prometheus_client.parser import text_string_to_metric_families
from ml.serving_release import export
from services.ab_api import app as api
from services.ab_api.runtime import ABRuntime,TrialConflict,TrialCapacity,UnknownMachine,cohort
from services.ab_api.metrics import exposition
from services.feature_api.runtime import FeatureRuntime,Unavailable
from services.predictor.runtime import PumpPredictor
from services.predictor.contracts import FeatureVector
from tests.test_serving_release import RegistryFixture

ROOT=Path(__file__).resolve().parents[2]
GOLDEN=json.loads((ROOT/'tests/fixtures/predictor-native-spark.json').read_text(encoding='utf-8'))
NOW=datetime(2026,10,27,22,tzinfo=timezone.utc);ASOF=datetime(2026,11,29,23,50,tzinfo=timezone.utc)


class Backend:
    """Keep genuine portable inference while controlling only native Feast reads and observed context."""
    def __init__(self,model,context):
        'Initialize a controllable model/backend fixture and its request counters.'
        self.model=model;self.context=context;self.calls=0;self.offset=0.;self.broken=False
    def fetch(self,machine):
        'Return a copied known feature vector with an optional controlled temperature shift.'
        values=deepcopy(GOLDEN['samples'][0]['features']);values['temperature_mean_10m']+=self.offset
        return {'values':values,'timestamps':{n:ASOF.isoformat() for n in values}}
    def predict(self,features):
        'Count model calls and optionally inject a private upstream failure.'
        self.calls+=1
        if self.broken:raise RuntimeError('private SDK detail must never reach clients')
        return {'model':self.model.metadata(),'predictions':self.model.predict([FeatureVector.model_validate(features)])}


def fixture_runtime(path,max_requests=2048,experiment='serve-jenkins-7'):
    """Use all200 fixture machines and an exporter-verified second identity; registry and Feast remain fixtures."""
    legacy=PumpPredictor(ROOT/'models/pump-failure-v1');registry=RegistryFixture(incremental=True);registry.version.version='2'
    bundle=path/'candidate'
    if not bundle.exists():export(registry,'2',bundle,ROOT/'ml/protocols/medium-60d-v1.json')
    candidate=PumpPredictor(bundle)
    context={'clock_mode':'simulation','as_of':ASOF.isoformat(),'prepared_wall_utc':NOW.isoformat(),
        'protocol_sha256':legacy.pin['protocol_sha256'],'source_manifest_sha256':'f'*64,
        'machines':{f'P-{i:03}':{'status':'operating','event_ts':ASOF.isoformat(),'received_ts':ASOF.isoformat(),'event_id':f'event-{i}'} for i in range(1,201)}}
    runtimes={name:FeatureRuntime(context,Backend(model,context),clock=lambda:NOW,model_pin=model.pin) for name,model in [('baseline',legacy),('candidate',candidate)]}
    reference={'format':'iot-drift-reference-v1','lineage':{'protocol_sha256':legacy.pin['protocol_sha256'],'fixture':True},
        'policy':{'bins':10,'psi_threshold':.2,'drifted_feature_fraction':.25,'pseudocount':.5},
        'samples':[deepcopy(GOLDEN['samples'][0]['features']) for _ in range(64)]}
    return ABRuntime(experiment,runtimes,reference,path/'trials.sqlite',max_requests)


@pytest.fixture
def runtime(tmp_path):'Provide an isolated A/B runtime fixture under the temporary test directory.';return fixture_runtime(tmp_path)


def populate(runtime,cycles=2):
    """Send distinct request IDs while deliberately replaying the same feature snapshots in each cycle."""
    return [runtime.predict(machine,f'trial-{cycle}-{machine}') for cycle in range(cycles) for machine in runtime.context['machines']]


def test_machine_assignment_is_stable_and_independent_of_request_order(runtime):
    'Verify machine assignment is stable and independent of request order.'
    machines=list(runtime.context['machines']);groups={cohort(runtime.experiment,m) for m in machines};assert groups=={'baseline','candidate'}
    first=[runtime.predict(m,'initial-'+m) for m in machines[:8]]
    second=[runtime.predict(m,'second-'+m) for m in reversed(machines[:8])]
    assert {r['machine_id']:r['variant'] for r in first}=={r['machine_id']:r['variant'] for r in second}
    assert sum(runtime.snapshot()['metrics'][v]['requests'] for v in groups)==16


@given(st.lists(st.tuples(st.integers(min_value=1,max_value=4),st.integers(min_value=1,max_value=6)),min_size=1,max_size=24))
@settings(max_examples=15,deadline=None)
def test_arbitrary_request_replays_and_conflicts_obey_set_counting(actions):
    'Verify arbitrary request replays and conflicts obey set counting.'
    with TemporaryDirectory() as directory:
        runtime=fixture_runtime(Path(directory));seen={}
        for number,identity in actions:
            machine=f'P-{number:03}';request_id=f'request-{identity}'
            if request_id in seen and seen[request_id]['machine_id']!=machine:
                with pytest.raises(TrialConflict):runtime.predict(machine,request_id)
            else:
                result=runtime.predict(machine,request_id)
                if request_id in seen:assert result==seen[request_id]
                seen[request_id]=result
        snapshot=runtime.snapshot()
        assert {row['request_id'] for row in snapshot['observations']}==set(seen)
        assert sum(value['requests'] for value in snapshot['metrics'].values())==len(seen)


def test_real_portable_prediction_pin_and_trace(runtime):
    'Verify real portable prediction pin and trace.'
    row=runtime.predict('P-001','known')
    assert row['status']==200 and row['prediction']['risk_score']==pytest.approx(GOLDEN['samples'][0]['expected_risk_score'],abs=1e-12)
    assert all(row['model'][name]==value for name,value in runtime.pins[row['variant']].items()) and row['feature_trace']['machine_id']=='P-001'
    assert row['latency_ms']>=0 and row['experiment']==runtime.experiment


def test_invalid_initial_config_preserves_existing_database(runtime,tmp_path):
    'Verify invalid initial config preserves existing database.'
    reference={'format':'iot-drift-reference-v1','lineage':{'protocol_sha256':runtime.context['protocol_sha256']},
        'policy':runtime.detector.policy,'samples':[GOLDEN['samples'][0]['features']]*64}
    for bad in ['',None,'a'*65,'../escape','line\nbreak']:
        with pytest.raises(ValueError):ABRuntime(bad,runtime.runtimes,reference,tmp_path/'bad')
    for bad in [True,0,2049,1.5]:
        with pytest.raises(ValueError):ABRuntime('x',runtime.runtimes,reference,tmp_path/'bad',bad)
    with pytest.raises(ValueError):ABRuntime('x',runtime.runtimes,reference,':memory:')
    with pytest.raises(ValueError):ABRuntime('x',{'baseline':runtime.runtimes['baseline']},reference,tmp_path/'bad')
    for mode in ['same-version','not-production','context','protocol','reference']:
        variants=deepcopy(runtime.runtimes);ref=deepcopy(reference)
        if mode=='same-version':variants['candidate'].model_pin['version']='1'
        elif mode=='not-production':variants['candidate'].model_pin['production']=False
        elif mode=='context':variants['candidate'].context=deepcopy(runtime.context);variants['candidate'].context['as_of']='2026-11-28T00:00:00Z'
        elif mode=='protocol':variants['candidate'].model_pin['protocol_sha256']='a'*64
        else:ref['lineage']['protocol_sha256']='b'*64
        with pytest.raises(ValueError):ABRuntime('bad',variants,ref,tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_database_identity_and_retention_cannot_be_reinterpreted(runtime,tmp_path):
    'Verify database identity and retention cannot be reinterpreted.'
    first=runtime.predict('P-001','once');restarted=fixture_runtime(tmp_path)
    assert restarted.predict('P-001','once')==first
    with pytest.raises(TrialConflict):fixture_runtime(tmp_path,experiment='different')
    with pytest.raises(TrialConflict):fixture_runtime(tmp_path,max_requests=1)
    assert runtime.snapshot()['observations']==[first]


def test_concurrent_replay_across_connections_calls_model_once(runtime,tmp_path):
    'Verify concurrent replay across connections calls model once.'
    other=fixture_runtime(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda i:[runtime,other][i%2].predict('P-001','same-request'),range(24)))
    assert all(row==results[0] for row in results)
    assert sum(r.backend.calls for instance in [runtime,other] for r in instance.runtimes.values())==1
    with pytest.raises(TrialConflict):other.predict('P-002','same-request')
    assert len(other.snapshot()['observations'])==1


def test_request_boundaries_unknown_machine_and_capacity(tmp_path):
    'Verify request boundaries unknown machine and capacity.'
    runtime=fixture_runtime(tmp_path,max_requests=1)
    for bad in ['',None,'a'*101,'../x','a b']:
        with pytest.raises(ValueError):runtime.predict('P-001',bad)
    with pytest.raises(UnknownMachine):runtime.predict('P-999','unknown')
    assert runtime.predict('P-001','a'*100)['status']==200
    assert runtime.predict('P-001','a'*100)['status']==200
    with pytest.raises(TrialCapacity):runtime.predict('P-002','next')


def test_failed_and_stopped_trials_are_persistent_outcomes(runtime):
    'Verify failed and stopped trials are persistent outcomes.'
    machine='P-001';variant=cohort(runtime.experiment,machine);runtime.runtimes[variant].backend.broken=True
    a=runtime.predict(machine,'failed');assert a['status']==503 and 'private SDK' not in json.dumps(a)
    runtime.runtimes[variant].backend.broken=False;assert runtime.predict(machine,'failed')==a
    for r in runtime.runtimes.values():r.context['machines']['P-002']['status']='maintenance'
    stopped=runtime.predict('P-002','stopped');assert stopped['status']==409
    snapshot=runtime.snapshot();assert sum(m['failures'] for m in snapshot['metrics'].values())==2
    assert sum(d['unique_rows'] for d in snapshot['drift'].values())==0


def test_pins_do_not_change_when_backend_or_runtime_is_reconfigured(runtime):
    'Verify pins do not change when backend or runtime is reconfigured.'
    machine='P-001';variant=cohort(runtime.experiment,machine);runtime.runtimes[variant].model_pin['threshold']=.1
    assert runtime.predict(machine,'mismatched')['status']==503
    runtime.runtimes[variant].backend.model.pin['threshold']=.1
    assert runtime.predict(machine,'mismatched-again')['status']==503


def test_readiness_rechecks_the_original_pair_even_if_adapter_pin_changes(runtime):
    'Verify readiness rechecks the original pair even if adapter pin changes.'
    runtime.runtimes['baseline'].model_pin['threshold']=.1
    runtime.runtimes['baseline'].backend.model.pin['threshold']=.1
    with pytest.raises(ValueError,match='Readiness model identity'):runtime.ready()
    assert runtime.snapshot()['observations']==[]


def test_cached_trials_cannot_outlive_real_lease(runtime):
    'Verify cached trials cannot outlive real lease.'
    runtime.predict('P-001','old')
    for offset in [-1,3600,3601]:
        for r in runtime.runtimes.values():r.clock=lambda:NOW+timedelta(seconds=offset)
        with pytest.raises(Unavailable):runtime.predict('P-001','old')
    for r in runtime.runtimes.values():r.clock=lambda:NOW+timedelta(seconds=3599)
    assert runtime.predict('P-001','old')['status']==200


def test_drift_uses_unique_snapshots_and_independent_psi_arithmetic(runtime):
    'Verify drift uses unique snapshots and independent psi arithmetic.'
    populate(runtime);snapshot=runtime.snapshot()
    assert sum(m['requests'] for m in snapshot['metrics'].values())==400
    assert sum(d['unique_rows'] for d in snapshot['drift'].values())==200
    for variant in ['baseline','candidate']:
        drift=snapshot['drift'][variant];n=drift['unique_rows'];assert drift['ready'] and len(drift['features'])==8
        assert n*2==snapshot['metrics'][variant]['requests']
        # Constant feature has two bins [0,n]; independently calculate the declared half-count correction.
        p=[.5/65,64.5/65];q=[.5/(n+1),(n+.5)/(n+1)]
        expected=sum((b-a)*math.log(b/a) for a,b in zip(p,q))
        assert all(value['psi']==pytest.approx(expected) and value['current_counts']==[0,n] for value in drift['features'].values())
    assert snapshot['ground_truth_used'] is False


def test_conflicting_snapshot_is_rejected_even_before_drift_minimum(runtime):
    'Verify conflicting snapshot is rejected even before drift minimum.'
    runtime.predict('P-001','original');variant=cohort(runtime.experiment,'P-001');runtime.runtimes[variant].backend.offset=-20
    runtime.predict('P-001','changed')
    with pytest.raises(ValueError,match='Conflicting'):runtime.snapshot()


def test_shift_monitor_and_metrics_are_real_ledger_readbacks(runtime):
    'Verify shift monitor and metrics are real ledger readbacks.'
    for r in runtime.runtimes.values():r.backend.offset=-20
    populate(runtime);data=runtime.snapshot();raw=exposition(data).decode()
    samples={s.name+str(s.labels):s for family in text_string_to_metric_families(raw) for s in family.samples}
    requests=[s for s in samples.values() if s.name=='iot_ab_requests_total'];assert sum(s.value for s in requests)==400
    assert len(requests)==2 and {s.labels['variant'] for s in requests}=={'baseline','candidate'}
    assert sum(s.value for s in samples.values() if s.name=='iot_ab_request_seconds_count')==400
    assert 'P-001' not in raw and 'trial-' not in raw
    for drift in data['drift'].values():assert drift['features']['temperature_mean_10m']['psi']>.2
    assert len([s for s in samples.values() if s.name=='iot_ab_feature_psi'])==16
    assert sum(s.value for s in samples.values() if s.name=='iot_ab_failures_total')==0


def test_empty_metrics_has_no_fake_drift_or_mean(runtime):
    'Verify empty metrics has no fake drift or mean.'
    raw=exposition(runtime.snapshot()).decode();samples=[s for f in text_string_to_metric_families(raw) for s in f.samples]
    assert all(s.value==0 for s in samples if s.name.endswith(('_total','_count','_sum','_snapshots')))
    assert not any(s.name in ['iot_ab_feature_psi','iot_ab_mean_risk_score'] for s in samples)


def test_http_success_boundary_validation_and_replay(runtime):
    'Verify http success boundary validation and replay.'
    app=api.create_app(runtime)
    with TestClient(app) as client:
        assert client.get('/health/live').json()=={'live':True}
        assert client.get('/health/ready').json()['ready'] and runtime.snapshot()['observations']==[]
        headers={'X-Request-ID':'http-1'};a=client.post('/v1/machines/P-001:predict',json={},headers=headers)
        assert a.status_code==200 and client.post('/v1/machines/P-001:predict',json={},headers=headers).content==a.content
        assert client.post('/v1/machines/P-002:predict',json={},headers=headers).status_code==409
        for target,body,h in [('/v1/machines/P-01:predict',{},headers),('/v1/machines/P-001:predict',{},{}),
                ('/v1/machines/P-001:predict',{'failure_in_next_1h':1},headers),('/v1/machines/P-001:predict',{'variant':'candidate'},headers),
                ('/v1/machines/P-001:predict',[],headers),('/v1/machines/P-001:predict',{}, {'X-Request-ID':'a'*101})]:
            assert client.post(target,json=body,headers=h).status_code==422
        assert client.post('/v1/machines/P-999:predict',json={},headers={'X-Request-ID':'unknown'}).status_code==404
        assert client.post('/v1/machines/P-001:predict',content='x'*257,headers=headers).status_code==413
        assert client.post('/v1/machines/P-001:predict',content=iter([b'{}']),headers=headers).status_code==411
        assert client.get('/v1/experiment').json()['metrics']==runtime.snapshot()['metrics']
        assert 'iot_ab_requests_total' in client.get('/metrics').text
    assert app.state.runtime is None
    assert TestClient(app).get('/health/ready').status_code==503


def test_http_capacity_failures_and_unavailable_monitoring(tmp_path):
    'Verify http capacity failures and unavailable monitoring.'
    runtime=fixture_runtime(tmp_path,max_requests=1)
    with TestClient(api.create_app(runtime)) as client:
        runtime.runtimes[cohort(runtime.experiment,'P-001')].backend.broken=True
        assert client.post('/v1/machines/P-001:predict',json={},headers={'X-Request-ID':'failure'}).status_code==503
        assert client.post('/v1/machines/P-002:predict',json={},headers={'X-Request-ID':'full'}).status_code==507
        assert client.get('/health/ready').status_code==503
        with patch.object(runtime,'snapshot',side_effect=KeyError('internal-corrupt-field')):
            assert client.get('/metrics').status_code==503


def test_nonfinite_or_malformed_rejected_input_always_has_safe_json_errors(runtime):
    'Verify nonfinite or malformed rejected input always has safe json errors.'
    with TestClient(api.create_app(runtime)) as client:
        for body in [b'{"failure_in_next_1h":NaN}',b'{"variant":Infinity}',b'[NaN]',b'{']:
            reply=client.post('/v1/machines/P-001:predict',content=body,headers={'Content-Type':'application/json','X-Request-ID':'invalid'})
            assert reply.status_code==422 and isinstance(reply.json()['detail'],list)
            assert all('input' not in error for error in reply.json()['detail'])
        assert runtime.snapshot()['observations']==[]


def test_native_startup_uses_existing_sdk_state_and_two_fixed_urls(runtime,tmp_path,monkeypatch):
    'Verify native startup uses existing sdk state and two fixed urls.'
    config={'experiment':'native-fixture',**{name:{'url':'http://'+name+'.svc.cluster.local','pin':runtime.pins[name]} for name in runtime.runtimes}}
    source=tmp_path/'config.json';source.write_text(json.dumps(config));reference=tmp_path/'reference.json'
    reference.write_text(json.dumps({'format':'iot-drift-reference-v1','lineage':runtime.detector.lineage,
        'policy':runtime.detector.policy,'samples':[GOLDEN['samples'][0]['features']]*64}))
    for key,value in {'IOT_AB_CONFIG':str(source),'IOT_SERVING_STATE':'/fixture-state','IOT_DRIFT_REFERENCE':str(reference),'IOT_AB_DATABASE':str(tmp_path/'native.sqlite')}.items():monkeypatch.setenv(key,value)
    def backend(state,url):
        'Select the prepared baseline or candidate backend for factory injection.'
        assert state=='/fixture-state';return runtime.runtimes[url.split('//')[1].split('.')[0]].backend
    def feature(context,backend,model_pin):
        'Construct the fixture runtime with a deterministic clock and explicit model pin.'
        return FeatureRuntime(context,backend,clock=lambda:NOW,model_pin=model_pin)
    with patch.object(api,'FeastKServeBackend',side_effect=backend) as called,patch.object(api,'FeatureRuntime',side_effect=feature):
        with TestClient(api.create_app()) as client:
            assert client.get('/v1/experiment').json()['experiment']=='native-fixture'
            assert client.get('/health/ready').status_code==200
            reply=client.post('/v1/machines/P-001:predict',json={},headers={'X-Request-ID':'native-wiring'})
            assert reply.status_code==200 and reply.json()['prediction']['risk_score']==pytest.approx(GOLDEN['samples'][0]['expected_risk_score'],abs=1e-12)
    assert called.call_count==2


@pytest.mark.parametrize('machine,expected',[('P-0277','baseline'),('P-0192','candidate')])
def test_assignment_matches_published_half_split_vectors(machine,expected):
    """The published vectors start with7f and80, exercising both sides of the byte128 boundary."""
    assert cohort('Serve_1:x',machine)==expected


def test_readiness_exposes_the_complete_pair_without_creating_trials(runtime):
    """Deployment consumers need both immutable pins and the exact drift reference, not just a boolean."""
    with TestClient(api.create_app(runtime)) as client:
        assert client.get('/health/ready').json()=={'ready':True,'experiment':runtime.experiment,
            'reference_id':runtime.detector.reference_id,'pins':runtime.pins}
    assert runtime.snapshot()['observations']==[]
    assert [r.backend.calls for r in runtime.runtimes.values()]==[1,1]


def test_nested_persistent_ledger_preserves_its_external_identity_schema(runtime,tmp_path):
    """A read-only forensic consumer must recover the release/context/capacity binding after restart."""
    path=tmp_path/'new-volume'/'experiment'/'trials.sqlite'
    reference={'format':'iot-drift-reference-v1','lineage':runtime.detector.lineage,
        'policy':runtime.detector.policy,'samples':[deepcopy(GOLDEN['samples'][0]['features']) for _ in range(64)]}
    first=ABRuntime(runtime.experiment,runtime.runtimes,reference,path,32)
    response=first.predict('P-001','durable-once')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as database:
        identity=json.loads(database.execute('SELECT seal FROM identity WHERE id=1').fetchone()[0])
        assert identity=={'experiment':runtime.experiment,'pins':runtime.pins,'context':runtime.context,
            'reference_id':runtime.detector.reference_id,'max_requests':32}
        assert database.execute('SELECT id,machine,variant FROM trials').fetchall()==[
            ('durable-once','P-001',response['variant'])]
    restarted=ABRuntime(runtime.experiment,runtime.runtimes,reference,path,32)
    assert restarted.predict('P-001','durable-once')==response
    assert sum(r.backend.calls for r in runtime.runtimes.values())==1


def test_metric_values_labels_and_histogram_boundaries_from_known_ledger():
    """Different cohorts, errors, means and exact bucket edges must remain consumable by PromQL."""
    durations=[0,5,10,25,50,100,250,500,1000,5000,5001]
    rows=[{'variant':'baseline','latency_ms':value,'status':200 if i<9 else 503,
        **({'prediction':{'risk_score':.9 if i==8 else 0.}} if i<9 else {})} for i,value in enumerate(durations)]
    rows+=[{'variant':'candidate','latency_ms':20,'status':200,'prediction':{'risk_score':.8}},
        {'variant':'candidate','latency_ms':8000,'status':409}]
    snapshot={'experiment':'metric-oracle','baseline_pin':{'version':'1'},'candidate_pin':{'version':'6'},
        'observations':rows,'metrics':{'baseline':{'requests':11,'failures':2},'candidate':{'requests':2,'failures':1}},
        'drift':{'baseline':{'unique_rows':9,'ready':True,'features':{'temperature_mean_10m':{'psi':.125}}},
            'candidate':{'unique_rows':1,'ready':False}}}
    families=list(text_string_to_metric_families(exposition(snapshot).decode()))
    assert {f.name:f.type for f in families}=={'iot_ab_requests':'counter','iot_ab_failures':'counter',
        'iot_ab_request_seconds':'histogram','iot_ab_mean_risk_score':'gauge','iot_ab_unique_snapshots':'gauge','iot_ab_feature_psi':'gauge'}
    samples=[s for f in families for s in f.samples]
    expected={'baseline':{'iot_ab_requests_total':11,'iot_ab_failures_total':2,'iot_ab_mean_risk_score':.1,
        'iot_ab_unique_snapshots':9,'iot_ab_request_seconds_count':11,'iot_ab_request_seconds_sum':11.941},
        'candidate':{'iot_ab_requests_total':2,'iot_ab_failures_total':1,'iot_ab_mean_risk_score':.8,
        'iot_ab_unique_snapshots':1,'iot_ab_request_seconds_count':2,'iot_ab_request_seconds_sum':8.02}}
    buckets={'baseline':[2,3,4,5,6,7,8,9,10,11],'candidate':[0,0,1,1,1,1,1,1,1,2]}
    edges=['0.005','0.01','0.025','0.05','0.1','0.25','0.5','1','5','+Inf']
    for variant,version in [('baseline','1'),('candidate','6')]:
        labels={'experiment':'metric-oracle','variant':variant,'version':version}
        ordinary={s.name:s.value for s in samples if s.labels==labels}
        assert ordinary==pytest.approx(expected[variant],abs=1e-12)
        actual={s.labels['le']:s.value for s in samples if s.name=='iot_ab_request_seconds_bucket' and s.labels.get('variant')==variant}
        assert actual==dict(zip(edges,buckets[variant],strict=True))
        assert all(s.labels=={**labels,'le':s.labels['le']} for s in samples if s.name=='iot_ab_request_seconds_bucket' and s.labels.get('variant')==variant)
    drift=[s for s in samples if s.name=='iot_ab_feature_psi']
    assert len(drift)==1 and drift[0].value==.125 and drift[0].labels=={
        'experiment':'metric-oracle','variant':'baseline','version':'1','feature':'temperature_mean_10m'}


def test_health_remains_async_while_model_read_blocks(runtime):
    'Verify health remains async while model read blocks.'
    entered=threading.Event();release=threading.Event()
    def slow():'Block readiness until released so the async health path can be tested.';entered.set();assert release.wait(5);return {'ready':True}
    async def scenario():
        'Exercise readiness and liveness concurrently through the actual ASGI application.'
        app=api.create_app(runtime)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                with patch.object(runtime,'ready',side_effect=slow):
                    task=asyncio.create_task(client.get('/health/ready'))
                    try:
                        assert await asyncio.to_thread(entered.wait,3)
                        assert (await client.get('/health/live')).status_code==200
                    finally:release.set()
                    assert (await task).status_code==200
    asyncio.run(scenario())
