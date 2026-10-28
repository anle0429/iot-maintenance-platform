"""Test known-answer freshness, arrival eligibility, fail-closed API responses and replay invariance before native deployment."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace,ModuleType
from hashlib import sha256
import sys
import asyncio,json
import threading,time
from concurrent.futures import ThreadPoolExecutor
import httpx,pytest
from hypothesis import given,settings,strategies as st
from fastapi.testclient import TestClient
from services.feature_api.app import create_app
from services.feature_api.runtime import FeatureRuntime,Unavailable,NotOperating,aware,FeastKServeBackend
from services.predictor.runtime import PumpPredictor
from services.predictor.contracts import FeatureVector

ROOT=Path(__file__).resolve().parents[2];BUNDLE=ROOT/'models/pump-failure-v1'
GOLDEN=json.loads((ROOT/'tests/fixtures/predictor-native-spark.json').read_text())
NOW=datetime(2026,10,27,12,tzinfo=timezone.utc);ASOF=datetime(2026,11,29,23,50,tzinfo=timezone.utc)


class Backend:
    """Model a native response, record downstream calls and use the actual sealed portable model."""
    def __init__(self):
        'Prepare a copied golden vector, native timestamps and a real portable predictor fixture.'
        self.values=deepcopy(GOLDEN['samples'][0]['features']);self.timestamps={n:ASOF.isoformat() for n in self.values};self.calls=0
        self.model=PumpPredictor(BUNDLE)
    def fetch(self,machine):'Return copies so each request cannot mutate the fixture state.';return {'values':deepcopy(self.values),'timestamps':deepcopy(self.timestamps)}
    def predict(self,features):
        'Count prediction calls and evaluate the validated vector with the golden model.'
        self.calls+=1
        return {'model':self.model.metadata(),'predictions':self.model.predict([FeatureVector.model_validate(features)])}


def fixture_runtime():
    """Use a manually specified logical clock and an independently received operating event."""
    backend=Backend();context={'clock_mode':'simulation','as_of':ASOF.isoformat(),'prepared_wall_utc':NOW.isoformat(),
        'protocol_sha256':GOLDEN['samples'] and backend.model.metadata()['protocol_sha256'],'source_manifest_sha256':'source',
        'machines':{'P-001':{'status':'operating','event_ts':(ASOF-timedelta(seconds=30)).isoformat(),'received_ts':ASOF.isoformat(),'event_id':'event-A'}}}
    return FeatureRuntime(context,backend,clock=lambda:NOW)


def test_native_model_and_feature_trace():
    'Verify native model and feature trace.'
    r=fixture_runtime();a=r.predict('P-001')
    assert a['prediction']['risk_score']==pytest.approx(GOLDEN['samples'][0]['expected_risk_score'],abs=1e-12)
    assert a['prediction']['predicted_failure']==1 and a['feature_trace']['features']==r.backend.values
    assert a['model']['run_id']==GOLDEN['run_id'] and a['feature_trace']['operating_context']['event_id']=='event-A'


def test_complete_provenance_and_feature_contract():
    'Verify complete provenance and feature contract.'
    r=fixture_runtime();features=r.features('P-001')
    expected={'machine_id':'P-001','features':deepcopy(GOLDEN['samples'][0]['features']),
        'feature_event_ts':ASOF.isoformat(),'feature_age_seconds':0.0,'operating_context':r.context['machines']['P-001'],
        'as_of':ASOF.isoformat(),'clock_mode':'simulation','prepared_wall_utc':NOW.isoformat(),
        'protocol_sha256':r.backend.model.metadata()['protocol_sha256'],'source_manifest_sha256':'source'}
    assert features==expected
    result=r.predict('P-001');assert set(result)=={'prediction','model','feature_trace'}
    assert result['feature_trace']==expected and result['model']==r.backend.model.metadata()


def test_default_wall_clock_uses_aware_current_time():
    'Verify default wall clock uses aware current time.'
    r=fixture_runtime();r.context['prepared_wall_utc']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    actual=FeatureRuntime(r.context,r.backend).features('P-001')
    assert actual['prepared_wall_utc']==r.context['prepared_wall_utc'] and actual['as_of']==ASOF.isoformat()


def test_timestamp_formats_normalize_to_utc():
    'Verify timestamp formats normalize to utc.'
    expected=datetime(2026,1,1,6,30,tzinfo=timezone.utc)
    assert aware('2026-01-01T12:00:00+05:30')==expected
    assert aware('2026-01-01T06:30:00Z').isoformat()=='2026-01-01T06:30:00+00:00'


@pytest.mark.skipif(not hasattr(time,'tzset'),reason='Process timezone switching is a POSIX-only native check')
def test_server_timezone_cannot_change_returned_timestamp(monkeypatch):
    'Verify server timezone cannot change returned timestamp.'
    import os
    before=os.environ.get('TZ');monkeypatch.setenv('TZ','America/Chicago');time.tzset()
    try:assert aware('2026-01-01T12:00:00+05:30').isoformat()=='2026-01-01T06:30:00+00:00'
    finally:
        if before is None:os.environ.pop('TZ',None)
        else:os.environ['TZ']=before
        time.tzset()


@pytest.mark.parametrize('seconds,accepted',[(0,True),(599,True),(600,True),(601,False),(-1,False)])
def test_feature_age_boundaries(seconds,accepted):
    'Verify feature age boundaries.'
    r=fixture_runtime();r.backend.timestamps={n:(ASOF-timedelta(seconds=seconds)).isoformat() for n in r.backend.values}
    if accepted:assert r.features('P-001')['feature_age_seconds']==seconds
    else:
        with pytest.raises(Unavailable):r.features('P-001')


@pytest.mark.parametrize('seconds,accepted',[(0,True),(3599,True),(3600,False),(-1,False)])
def test_wall_lease_boundaries(seconds,accepted):
    'Verify wall lease boundaries.'
    r=fixture_runtime();r.clock=lambda:NOW+timedelta(seconds=seconds)
    if accepted:assert r.features('P-001')['clock_mode']=='simulation'
    else:
        with pytest.raises(Unavailable):r.features('P-001')


@pytest.mark.parametrize('field,value',[('received_ts',(ASOF+timedelta(seconds=1)).isoformat()),('event_ts',(ASOF+timedelta(seconds=1)).isoformat()),('event_ts',(ASOF-timedelta(seconds=601)).isoformat()),('status','unknown')])
def test_arrival_and_context_rejection(field,value):
    'Verify arrival and context rejection.'
    r=fixture_runtime();r.context['machines']['P-001'][field]=value
    with pytest.raises(Unavailable):r.predict('P-001')
    assert r.backend.calls==0


@pytest.mark.parametrize('status',['failed','maintenance'])
def test_stopped_machine_does_not_call_model(status):
    'Verify stopped machine does not call model.'
    r=fixture_runtime();r.context['machines']['P-001']['status']=status
    assert r.features('P-001')['operating_context']['status']==status
    with pytest.raises(NotOperating):r.predict('P-001')
    assert r.backend.calls==0


@pytest.mark.parametrize('change',['missing','extra','missing_timestamp','inconsistent_timestamp','nonfinite','no_readings','string_count'])
def test_native_bad_vector_is_rejected(change):
    'Verify native bad vector is rejected.'
    r=fixture_runtime();n=next(iter(r.backend.values))
    if change=='missing':del r.backend.values[n]
    elif change=='extra':r.backend.values['failure_in_next_1h']=1
    elif change=='missing_timestamp':del r.backend.timestamps[n]
    elif change=='inconsistent_timestamp':r.backend.timestamps[n]=(ASOF-timedelta(seconds=1)).isoformat()
    elif change=='nonfinite':r.backend.values[n]=float('nan')
    elif change=='no_readings':r.backend.values['reading_count_10m']=0
    else:r.backend.values['reading_count_10m']='10'
    with pytest.raises(ValueError):r.predict('P-001')
    assert r.backend.calls==0


@pytest.mark.parametrize('field,value',[('name','other'),('version','2'),('portable_sha256','changed'),('protocol_sha256','changed'),('threshold',0.6)])
def test_model_provenance_rejection(field,value):
    'Verify model provenance rejection.'
    r=fixture_runtime();original=r.backend.predict
    def altered(features):
        'Corrupt one returned lineage field while preserving the otherwise valid prediction.'
        result=original(features);result['model'][field]=value;return result
    r.backend.predict=altered
    with pytest.raises(Unavailable):r.predict('P-001')


@pytest.mark.parametrize('prediction',[[],[{'risk_score':True,'predicted_failure':1}],[{'risk_score':float('nan'),'predicted_failure':0}],[{'risk_score':1.1,'predicted_failure':1}],[{'risk_score':.8,'predicted_failure':0}],[{'risk_score':.8,'predicted_failure':True}]])
def test_invalid_downstream_prediction(prediction):
    'Verify invalid downstream prediction.'
    r=fixture_runtime();metadata=r.backend.model.metadata();r.backend.predict=lambda f:{'model':metadata,'predictions':prediction}
    with pytest.raises(Unavailable):r.predict('P-001')


def test_unknown_machine_and_ambiguous_context():
    'Verify unknown machine and ambiguous context.'
    r=fixture_runtime()
    with pytest.raises(KeyError):r.features('P-999')
    with pytest.raises(Unavailable):aware('2026-01-01T00:00:00')
    for key,value in [('clock_mode','wall'),('machines',{})]:
        context=deepcopy(r.context);context[key]=value
        with pytest.raises(Unavailable):FeatureRuntime(context,r.backend)


def test_http_health_features_prediction_and_unknown():
    'Verify http health features prediction and unknown.'
    r=fixture_runtime()
    with TestClient(create_app(r)) as c:
        assert c.get('/health/live').json()=={'live':True}
        assert c.get('/health/ready').json()['as_of']==ASOF.isoformat()
        assert c.get('/v1/machines/P-001/features').json()['features']==r.backend.values
        assert c.post('/v1/machines/P-001:predict').json()['prediction']['predicted_failure']==1
        assert c.get('/v1/machines/P-999/features').status_code==404
        for n in ['bad','P-01','P-10000']:assert c.get('/v1/machines/'+n+'/features').status_code==422
        r.context['machines']['P-001']['status']='failed'
        assert c.post('/v1/machines/P-001:predict').status_code==409
        r.clock=lambda:NOW+timedelta(hours=1)
        assert c.get('/health/ready').status_code==503


@pytest.mark.parametrize('error',[KeyError('missing schema'),RuntimeError('secret upstream detail'),PermissionError('OS permission')])
def test_upstream_error_is_sanitized(error):
    'Verify upstream error is sanitized.'
    r=fixture_runtime()
    def fail(machine):'Raise the selected backend exception to verify the API error partition.';raise error
    r.backend.fetch=fail
    with TestClient(create_app(r)) as c:
        result=c.get('/v1/machines/P-001/features');assert result.status_code==503 and str(error) not in result.text


def test_readiness_before_lifespan():
    'Verify readiness before lifespan.'
    with TestClient(create_app(fixture_runtime())) as c:
        c.app.state.runtime=None
        assert c.get('/health/ready').status_code==503 and c.get('/v1/machines/P-001/features').status_code==503


@settings(max_examples=50,deadline=None)
@given(st.permutations(list(GOLDEN['samples'][0]['features'])))
def test_replay_and_key_order_are_invariant(order):
    'Verify replay and key order are invariant.'
    r=fixture_runtime();first=r.predict('P-001');r.backend.values={n:r.backend.values[n] for n in order}
    assert r.predict('P-001')==first


def test_concurrent_routes_do_not_block_health():
    'Verify concurrent routes do not block health.'
    async def scenario():
        'Exercise concurrent feature and health requests against a deliberately slow backend.'
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=24))
        r=fixture_runtime();original=r.backend.fetch;lock=threading.Lock();counts={'active':0,'peak':0}
        def slow(machine):
            'Measure overlapping backend calls while adding a controlled delay.'
            with lock:counts['active']+=1;counts['peak']=max(counts['active'],counts['peak'])
            try:time.sleep(.05);return original(machine)
            finally:
                with lock:counts['active']-=1
        r.backend.fetch=slow;app=create_app(r)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
                order=[]
                async def request(path):
                    'Record HTTP completion order while returning each real ASGI response.'
                    reply=await c.get(path);order.append(path);return reply
                replies=await asyncio.gather(*[request('/v1/machines/P-001/features') for _ in range(32)],request('/health/live'))
                assert all(x.status_code==200 for x in replies) and order[0]=='/health/live' and 2<=counts['peak']<=16
    asyncio.run(scenario())


@pytest.fixture
def sdk_adapter(tmp_path,monkeypatch):
    """Use a real captured Feast0.67 protobuf JSON response to test the SDK boundary without a Redis service on PC."""
    native=json.loads((ROOT/'tests/fixtures/feature-api-native-online.json').read_text())
    fields=dict(zip(native['metadata']['feature_names']['val'],native['results'],strict=True))
    raw={}
    for name,vector in fields.items():
        key,value=next(iter(vector['values'][0].items()))
        raw[name]=[int(value) if key=='int64_val' else value]
        raw[name+'__ts']=[int(aware(vector['event_timestamps'][0]).timestamp())]
    config=[];calls=[]
    class Store:
        'Record native Feast configuration and calls without opening an external store.'
        def __init__(self,**kwargs):'Record the RepoConfig arguments passed by the native adapter.';config.append(kwargs)
        def get_feature_service(self,name):'Return the requested service name as a minimal SDK fixture.';return name
        def get_online_features(self,**kwargs):
            'Record lookup arguments and expose the prepared native and dictionary responses.'
            calls.append(kwargs)
            def to_dict(**kwargs):'Record conversion options and return the original feature dictionary.';calls.append({'conversion':kwargs});return raw
            return SimpleNamespace(proto=native,to_dict=to_dict)
    feast=ModuleType('feast');feast.FeatureStore=Store
    repo=ModuleType('feast.repo_config');repo.RepoConfig=lambda **kw:kw
    protobuf=ModuleType('google.protobuf.json_format')
    def to_proto(value,**kwargs):'Record protobuf conversion options and return the supplied native fixture.';calls.append({'protobuf_conversion':kwargs});return value
    protobuf.MessageToDict=to_proto
    for n,m in [('feast',feast),('feast.repo_config',repo),('google.protobuf.json_format',protobuf)]:monkeypatch.setitem(sys.modules,n,m)
    monkeypatch.setenv('IOT_FEAST_REDIS','online:6379')
    for n,value in [('context.json',fixture_runtime().context),('verification.json',{'passed':True})]:
        (tmp_path/n).write_text(json.dumps(value))
    seal={n+'_sha256':sha256((tmp_path/(n+'.json')).read_bytes()).hexdigest() for n in ['verification','context']}
    (tmp_path/'_READY.json').write_text(json.dumps(seal))
    adapter=FeastKServeBackend(tmp_path,'http://private-service/')
    return adapter,native,raw,config,calls,tmp_path


def test_native_sdk_adapter_contract(sdk_adapter):
    'Verify native sdk adapter contract.'
    adapter,native,raw,config,calls,_=sdk_adapter
    vector=adapter.fetch('P-001')
    assert vector['values']['reading_count_10m']==9 and vector['values']['prior_failures_24h']==2
    assert set(vector['timestamps'].values())=={ASOF.isoformat()}
    assert calls==[{'features':'iot_failure_prediction_v1','entity_rows':[{'machine_id':'P-001'}]},
        {'conversion':{'include_event_timestamps':True}},{'protobuf_conversion':{'preserving_proto_field_name':True}}]
    assert config==[{'config':{'project':'iot_serving_demo','provider':'local','registry':str(sdk_adapter[-1]/'registry/registry.db'),
        'offline_store':{'type':'duckdb'},'online_store':{'type':'redis','connection_string':'online:6379,socket_timeout=2,socket_connect_timeout=2','key_ttl_seconds':3600}}}]
    assert adapter.context==fixture_runtime().context and adapter.endpoint=='http://private-service'
    assert vector['values']=={n:raw[n][0] for n in GOLDEN['samples'][0]['features']}


@pytest.mark.parametrize('change',['status','wrong_machine','corrupt_context','unverified'])
def test_native_adapter_fail_closed(sdk_adapter,change):
    'Verify native adapter fail closed.'
    adapter,native,raw,config,calls,state=sdk_adapter
    if change=='status':
        native['results'][1]['statuses']=['NOT_FOUND']
        with pytest.raises(Unavailable):adapter.fetch('P-001')
    elif change=='wrong_machine':
        raw['machine_id']=['P-999']
        with pytest.raises(Unavailable):adapter.fetch('P-001')
    else:
        if change=='corrupt_context':(state/'context.json').write_text('{}')
        else:(state/'verification.json').write_text(json.dumps({'passed':False}))
        with pytest.raises(Unavailable):FeastKServeBackend(state,'http://private-service')


def test_native_prediction_http_contract(sdk_adapter,monkeypatch):
    'Verify native prediction http contract.'
    adapter,*_=sdk_adapter;seen=[]
    class Reply:
        'Provide a context-managed fixed KServe response for the HTTP adapter test.'
        def __enter__(self):'Return the response fixture when entering its context manager.';return self
        def __exit__(self,*args):'Close the response fixture without suppressing any exception.';return None
        def read(self):'Return the fixed serialized KServe prediction payload.';return b'{"predictions":[{"risk_score":0.8,"predicted_failure":1}]}'
    def open_request(request,timeout):'Record the actual request and timeout before returning the fixed response.';seen.append((request,timeout));return Reply()
    monkeypatch.setattr('services.feature_api.runtime.urllib.request.urlopen',open_request)
    result=adapter.predict({'temperature_mean_10m':55})
    request,timeout=seen[0]
    assert request.full_url=='http://private-service/v1/models/iot-pump-failure-v1:predict' and timeout==3
    assert json.loads(request.data)=={'instances':[{'temperature_mean_10m':55}]} and result['predictions'][0]['risk_score']==.8
    assert request.get_header('Content-type')=='application/json' and request.get_method()=='POST'


def test_false_report_with_matching_checksum_is_still_rejected(sdk_adapter):
    'Verify false report with matching checksum is still rejected.'
    *_,state=sdk_adapter
    (state/'verification.json').write_text(json.dumps({'passed':False}))
    seal=json.loads((state/'_READY.json').read_text());seal['verification_sha256']=sha256((state/'verification.json').read_bytes()).hexdigest()
    (state/'_READY.json').write_text(json.dumps(seal))
    with pytest.raises(Unavailable):FeastKServeBackend(state,'http://service')


@pytest.mark.parametrize('direction',['missing','extra'])
def test_misaligned_native_metadata_is_rejected(sdk_adapter,direction):
    'Verify misaligned native metadata is rejected.'
    adapter,native,*_=sdk_adapter
    if direction=='missing':native['results'].pop()
    else:native['results'].append(deepcopy(native['results'][-1]))
    with pytest.raises(ValueError):adapter.fetch('P-001')


def test_http_error_and_readiness_contracts_are_complete():
    'Verify http error and readiness contracts are complete.'
    r=fixture_runtime()
    with TestClient(create_app(r)) as c:
        assert c.get('/health/ready').json()=={'ready':True,'clock_mode':'simulation','as_of':ASOF.isoformat()}
        assert c.get('/v1/machines/P-999/features').json()=={'detail':'Unknown machine'}
        r.context['machines']['P-001']['status']='failed'
        assert c.post('/v1/machines/P-001:predict').json()=={'detail':'Prediction is defined only while the machine is operating'}
        r.clock=lambda:NOW+timedelta(hours=1)
        assert c.get('/v1/machines/P-001/features').json()=={'detail':'Feature/model data is unavailable or failed validation'}
        c.app.state.runtime=None
        assert c.get('/health/ready').json()=={'detail':'Feature service is not ready'}


def test_production_lifespan_constructs_native_backend(monkeypatch):
    'Verify production lifespan constructs native backend.'
    r=fixture_runtime();monkeypatch.setenv('IOT_SERVING_STATE','/state');monkeypatch.setenv('IOT_KSERVE_URL','http://kserve')
    def backend(state,url):
        'Verify native backend factory arguments and return the prepared fixture.'
        assert state=='/state' and url=='http://kserve';r.backend.context=r.context;return r.backend
    monkeypatch.setattr('services.feature_api.app.FeastKServeBackend',backend)
    def runtime(context,b):
        'Verify runtime factory injection preserves the prepared context and backend.'
        assert context==r.context and b is r.backend;return r
    monkeypatch.setattr('services.feature_api.app.FeatureRuntime',runtime)
    with TestClient(create_app()) as c:assert c.get('/health/ready').status_code==200
