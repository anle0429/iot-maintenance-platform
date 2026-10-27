"""Exercise versioned serving configuration with real portable weights and explicit fixture-only registry receipts."""
from copy import deepcopy
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import socket
from threading import Thread
import time
from types import SimpleNamespace as NS
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from fastapi.testclient import TestClient
import pytest
import uvicorn

from services.predictor.app import create_app as predictor_app
from services.predictor.runtime import PumpPredictor
from services.predictor.binding import FIELDS,LEGACY,validate_pin
from services.predictor.contracts import FeatureVector
from services.feature_api.app import create_app as feature_app
from services.feature_api.runtime import FeatureRuntime,Unavailable
from ml.serving_release import export
from tests.test_serving_release import RegistryFixture

ROOT=Path(__file__).resolve().parents[2];BUNDLE=ROOT/'models/pump-failure-v1'
GOLDEN=json.loads((ROOT/'tests/fixtures/predictor-native-spark.json').read_text())
NOW=datetime(2026,10,27,12,tzinfo=timezone.utc);ASOF=datetime(2026,11,29,23,50,tzinfo=timezone.utc)


@pytest.fixture
def release(tmp_path):
    """Use the real exporter with fixture registry metadata and model1 weights; no live promotion occurs."""
    registry=RegistryFixture(incremental=True);registry.version.version='2'
    bundle=tmp_path/'fixture-release'
    export(registry,'2',bundle,ROOT/'ml/protocols/medium-60d-v1.json')
    return bundle


def set_lineage(bundle,change):
    """Alter fixture registry claims independently from coefficients to test startup authorization."""
    path=bundle/'lineage.json';value=json.loads(path.read_text());change(value);path.write_text(json.dumps(value))


def runtime(bundle):
    """Use native golden feature values and a separate manual simulation clock at the API boundary."""
    model=PumpPredictor(bundle)
    class Backend:
        def __init__(self):self.calls=0
        def fetch(self,machine):
            return {'values':deepcopy(GOLDEN['samples'][0]['features']),
                'timestamps':{n:ASOF.isoformat() for n in GOLDEN['samples'][0]['features']}}
        def predict(self,features):
            self.calls+=1
            return {'model':model.metadata(),'predictions':model.predict([FeatureVector.model_validate(features)])}
    backend=Backend();context={'clock_mode':'simulation','as_of':ASOF.isoformat(),'prepared_wall_utc':NOW.isoformat(),
        'protocol_sha256':LEGACY['protocol_sha256'],'source_manifest_sha256':'f'*64,
        'machines':{'P-001':{'status':'operating','event_ts':ASOF.isoformat(),'received_ts':ASOF.isoformat()}}}
    return FeatureRuntime(context,backend,clock=lambda:NOW,model_pin=model.pin)


def test_legacy_pin_asset_matches_loaded_model():
    assert json.loads((BUNDLE/'model-pin.json').read_text())==LEGACY==PumpPredictor(BUNDLE).pin


def test_versioned_predictor_http_preserves_native_goldens(release):
    with TestClient(predictor_app(release)) as client:
        assert client.get('/health/ready').json()=={'ready':True,'version':'2'}
        response=client.post('/v1/models/iot-pump-failure-v1:predict',json={'instances':[x['features'] for x in GOLDEN['samples']]})
        assert response.status_code==200
        assert response.json()['model']['version']=='2' and response.json()['model']['production'] is True
        for actual,expected in zip(response.json()['predictions'],GOLDEN['samples'],strict=True):
            assert actual['risk_score']==pytest.approx(expected['expected_risk_score'],abs=1e-12)
            assert actual['predicted_failure']==expected['expected_predicted_failure']


def test_feature_http_accepts_the_explicit_release_and_keeps_trace(release):
    r=runtime(release)
    with TestClient(feature_app(r)) as client:
        reply=client.post('/v1/machines/P-001:predict')
        assert reply.status_code==200 and reply.json()['model']['version']=='2'
        assert reply.json()['model']['production'] is True and reply.json()['feature_trace']['clock_mode']=='simulation'
        assert reply.json()['prediction']['risk_score']==pytest.approx(GOLDEN['samples'][0]['expected_risk_score'],abs=1e-12)


def test_release_threshold_is_used_at_the_exact_boundary(release):
    path=release/'portable.json';model=json.loads(path.read_text());model.update(coefficients=[0.0]*8,intercept=0.0,threshold=0.5)
    raw=json.dumps(model).encode();path.write_bytes(raw)
    set_lineage(release,lambda v:v.update(portable_sha256=sha256(raw).hexdigest()))
    r=runtime(release);reply=r.predict('P-001')
    assert reply['model']['threshold']==0.5 and reply['prediction']=={'risk_score':0.5,'predicted_failure':1}


@pytest.mark.parametrize('field,value',[(n,'changed') for n in FIELDS if n not in ['production','threshold']]
    +[('production',False),('production',1),('threshold',0.6)])
def test_any_wrong_downstream_identity_is_blocked(release,field,value):
    r=runtime(release);original=r.backend.predict
    def altered(features):
        reply=original(features);reply['model'][field]=value;return reply
    r.backend.predict=altered
    with TestClient(feature_app(r)) as client:
        assert client.post('/v1/machines/P-001:predict').status_code==503


@pytest.mark.parametrize('change',[
    lambda v:v.pop('production'),lambda v:v.update(production=False),lambda v:v.update(production='true'),
    lambda v:v.update(registry_selection=[]),lambda v:v.pop('registry_selection'),
    lambda v:v['registry_selection'].update(name='other'),lambda v:v['registry_selection'].update(version='3'),
    lambda v:v['registry_selection'].update(run_id='b'*32),lambda v:v['registry_selection'].update(registry_stage='Archived'),
    lambda v:v['registry_selection'].update(production_tag='false'),lambda v:v.update(registry_uri='models:/other/2')])
def test_new_model_cannot_start_with_unverified_registry_claim(release,change):
    set_lineage(release,change)
    with pytest.raises(ValueError):PumpPredictor(release)


def test_legacy_stage_production_claim_can_be_loaded(release):
    set_lineage(release,lambda v:v['registry_selection'].update(production_tag=None,registry_stage='Production'))
    assert PumpPredictor(release).metadata()['production'] is True


@pytest.mark.parametrize('field,value',[(n,'invalid') for n in ['name','version','run_id','protocol_sha256','portable_sha256','training_code_commit','data_version']]
    +[('version',1),('threshold',0),('threshold',1),('threshold',float('nan')),('threshold',True),('production','false')])
def test_pin_schema_rejects_invalid_identity_and_boundaries(field,value):
    pin=deepcopy(LEGACY);pin[field]=value
    with pytest.raises(ValueError):validate_pin(pin)


@pytest.mark.parametrize('pin',[{},[],None,{'version':'2'},{**LEGACY,'extra':'unsupported'}])
def test_explicit_pin_file_cannot_silently_fall_back(tmp_path,monkeypatch,pin):
    path=tmp_path/'pin.json';path.write_text(json.dumps(pin));monkeypatch.setenv('IOT_MODEL_PIN',str(path))
    def forbidden(*args):raise AssertionError('Invalid pin must fail before native SDK construction')
    monkeypatch.setattr('services.feature_api.app.FeastKServeBackend',forbidden)
    with pytest.raises(ValueError):
        with TestClient(feature_app()):pass


def test_feature_startup_reads_the_configured_pin_file(release,tmp_path,monkeypatch):
    fixture=runtime(release);pin=fixture.model_pin
    path=tmp_path/'pin.json';path.write_text(json.dumps(pin));monkeypatch.setenv('IOT_MODEL_PIN',str(path))
    monkeypatch.setenv('IOT_SERVING_STATE','/state');monkeypatch.setenv('IOT_KSERVE_URL','http://private')
    def backend(state,url):
        assert state=='/state' and url=='http://private';fixture.backend.context=fixture.context;return fixture.backend
    def create(context,b,model_pin):
        assert context==fixture.context and b is fixture.backend and model_pin==pin;return fixture
    monkeypatch.setattr('services.feature_api.app.FeastKServeBackend',backend)
    monkeypatch.setattr('services.feature_api.app.FeatureRuntime',create)
    with TestClient(feature_app()) as client:assert client.post('/v1/machines/P-001:predict').json()['model']['version']=='2'


def test_feature_pin_copy_and_runtime_protocol_changes_are_guarded(release):
    r=runtime(release);pin=deepcopy(r.model_pin)
    other=FeatureRuntime(r.context,r.backend,clock=lambda:NOW,model_pin=pin)
    pin['version']='3';assert other.model_pin['version']=='2'
    r.context['protocol_sha256']='f'*64
    with pytest.raises(Unavailable):r.predict('P-001')
    assert r.backend.calls==0
    with pytest.raises(Unavailable):FeatureRuntime(r.context,r.backend,model_pin=r.model_pin)


@pytest.mark.parametrize('change',[lambda r:r.context['machines']['P-001'].update(status='maintenance'),
    lambda r:setattr(r,'clock',lambda:NOW+timedelta(hours=1))])
def test_new_release_does_not_bypass_stopped_or_expired_feature_checks(release,change):
    r=runtime(release);change(r)
    with TestClient(feature_app(r)) as client:assert client.post('/v1/machines/P-001:predict').status_code in [409,503]
    assert r.backend.calls==0


def test_pin_writer_uses_validated_bundle_and_preserves_existing_output(release,tmp_path):
    spec=importlib.util.spec_from_file_location('pin_writer_test',ROOT/'scripts/write-model-pin.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    args=NS(bundle=release,output=tmp_path/'release-pin.json');module.run(args)
    expected=PumpPredictor(release).pin;assert json.loads(args.output.read_text())==expected
    raw=args.output.read_bytes()
    with pytest.raises(FileExistsError):module.run(args)
    assert args.output.read_bytes()==raw


@pytest.mark.parametrize('field,value',[('intercept',True),('intercept','0'),('mean',None),('std','bad')])
def test_nonfinite_or_wrong_numeric_types_never_start(release,field,value):
    path=release/'portable.json';model=json.loads(path.read_text());model[field]=value;raw=json.dumps(model).encode();path.write_bytes(raw)
    set_lineage(release,lambda v:v.update(portable_sha256=sha256(raw).hexdigest()))
    with pytest.raises(ValueError):PumpPredictor(release)


@contextmanager
def local_http(app):
    """Bind a private ephemeral loopback socket and always stop this test's own HTTP server."""
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
    thread=Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+10
        while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.01)
        assert server.started,'Loopback HTTP server did not become ready'
        yield 'http://127.0.0.1:'+str(port)
    finally:
        server.should_exit=True;thread.join(timeout=5);sock.close()
        assert not thread.is_alive(),'Own test HTTP server did not stop'


def test_version_pin_across_two_real_http_servers_under_bounded_load(release):
    """Exercise actual TCP, async adapter and model HTTP; Feast/context remain explicit small fixtures."""
    r=runtime(release)
    expected_model=PumpPredictor(release).metadata()
    with local_http(predictor_app(release)) as predictor_url:
        def predict(features):
            request=Request(predictor_url+'/v1/models/iot-pump-failure-v1:predict',
                data=json.dumps({'instances':[features]}).encode(),headers={'content-type':'application/json'})
            with urlopen(request,timeout=5) as response:return json.load(response)
        r.backend.predict=predict
        with local_http(feature_app(r)) as feature_url:
            def request(index):
                started=time.perf_counter()
                with urlopen(Request(feature_url+'/v1/machines/P-001:predict',data=b'',method='POST'),timeout=10) as response:
                    value=json.load(response);assert response.status==200 and value['model']==expected_model
                    assert value['prediction']['risk_score']==pytest.approx(GOLDEN['samples'][0]['expected_risk_score'],abs=1e-12)
                    assert value['prediction']['predicted_failure']==GOLDEN['samples'][0]['expected_predicted_failure']
                    assert value['feature_trace']['clock_mode']=='simulation'
                return (time.perf_counter()-started)*1000
            started=time.perf_counter()
            with ThreadPoolExecutor(max_workers=8) as pool:latencies=sorted(pool.map(request,range(128)))
            elapsed=time.perf_counter()-started
            original=deepcopy(r.model_pin)
            for field in FIELDS:
                r.model_pin={**original,field:None}
                with pytest.raises(HTTPError) as rejected:urlopen(Request(feature_url+'/v1/machines/P-001:predict',data=b'',method='POST'),timeout=5)
                assert rejected.value.code==503
            r.model_pin=original
            print(json.dumps({'scope':'PC loopback HTTP, fixture Feast/context, fixture version2 with real model1 weights; no deployment',
                'workers':8,'successful_requests':len(latencies),'failed_requests':0,'elapsed_seconds':elapsed,
                'p50_ms':latencies[len(latencies)//2],'p95_ms':latencies[int(.95*len(latencies))],
                'downstream_identity_fields_rejected':len(FIELDS)}))
