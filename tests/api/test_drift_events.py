"""Check empty acknowledgement, transactional replay and rejection before a drift event is stored."""
from copy import deepcopy
from fastapi.testclient import TestClient
from fastapi import HTTPException
from hypothesis import given,settings,strategies as st
import pytest
from services.drift_api.app import create_app
from services.drift_api.runtime import DriftRuntime
from services.drift_api.events import validate_envelope
from tests.api.test_drift_api import bundle,window


def headers():
    """Use the publisher's declared binary binding independently of its implementation."""
    return {'ce-specversion':'1.0','ce-type':'dev.edai.iot.drift.window',
            'ce-source':'urn:edai:iot:window-publisher','ce-id':'transport-1','content-type':'application/json'}


def test_empty_ack_after_persist_and_distinct_transport_ids_replay_one_window(tmp_path):
    'Verify empty ack after persist and distinct transport ids replay one window.'
    runtime=DriftRuntime(bundle(),tmp_path/'windows.db')
    with TestClient(create_app(runtime)) as client:
        for i in range(9):
            reply=client.post('/v1/events/drift',json=window(),headers={**headers(),'ce-id':f'transport-{i}'})
            assert reply.status_code==204 and reply.content==b''
        assert runtime.ready()['stored_windows']==1
        original=client.post('/v1/drift:compare',json=window()).json()
        changed=deepcopy(window());changed['observations'][0]['features']['current_mean_10m']+=1
        assert client.post('/v1/events/drift',json=changed,headers=headers()).status_code==409
        assert client.post('/v1/drift:compare',json=window()).json()==original


@pytest.mark.parametrize('name,value',[
    ('ce-specversion','0.3'),('ce-type','other'),('ce-source','urn:other'),
    ('ce-id',''),('ce-id','x'*101),('ce-id','bad id'),('content-type','text/plain'),
])
def test_invalid_envelope_cannot_write_the_store(name,value,tmp_path):
    'Verify invalid envelope cannot write the store.'
    runtime=DriftRuntime(bundle(),tmp_path/'windows.db')
    with TestClient(create_app(runtime)) as client:
        reply=client.post('/v1/events/drift',content=__import__('json').dumps(window()),headers={**headers(),name:value})
        assert reply.status_code in [400,422]
        assert runtime.ready()['stored_windows']==0


def test_metadata_boundaries_and_media_type_parameters(tmp_path):
    'Verify metadata boundaries and media type parameters.'
    runtime=DriftRuntime(bundle(),tmp_path/'windows.db')
    with TestClient(create_app(runtime)) as client:
        for identity in ['x','x'*100,'ABC_123:-']:
            reply=client.post('/v1/events/drift',json=window(),headers={**headers(),'ce-id':identity,'content-type':'application/json; charset=utf-8'})
            assert reply.status_code==204 and not reply.content
        assert client.post('/v1/events/drift',json=window()).status_code==400
        assert client.post('/v1/events/drift',json={},headers=headers()).status_code==422
        assert client.post('/v1/events/drift',content=iter([b'{}']),headers=headers()).status_code==411
        client.app.state.runtime=None
        assert client.post('/v1/events/drift',json=window(),headers=headers()).status_code==503


@given(st.text(alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_:-',min_size=1,max_size=100))
@settings(max_examples=35,deadline=None)
def test_property_event_identity_and_http_media_type_normalization(identity):
    """Every allowed identity is valid; another envelope's route metadata cannot bypass the contract."""
    value={**headers(),'ce-id':identity,'content-type':'  Application/JSON ; charset=utf-8'}
    validate_envelope(value)
    for name in ['ce-id','ce-type','ce-source','ce-specversion','content-type']:
        invalid={**value};invalid.pop(name)
        with pytest.raises(HTTPException) as error:validate_envelope(invalid)
        assert error.value.status_code==400
    with pytest.raises(HTTPException):validate_envelope({**value,'ce-id':identity+' '})
    with pytest.raises(HTTPException):validate_envelope({**value,'content-type':'application/cloudevents+json'})


def test_private_store_error_is_redacted_before_event_ack(tmp_path,monkeypatch):
    """A failed durable write must neither acknowledge delivery nor disclose backend diagnostics."""
    runtime=DriftRuntime(bundle(),tmp_path/'windows.db')
    def broken(*args):'Inject an upstream failure containing private diagnostics to test response redaction.';raise OSError('SYNTHETIC_PRIVATE_BACKEND_DIAGNOSTIC')
    monkeypatch.setattr(runtime,'compare',broken)
    with TestClient(create_app(runtime)) as client:
        response=client.post('/v1/events/drift',json=window(),headers=headers())
        assert response.status_code==503
        assert response.json()=={'detail':'Drift reference or persistent store is unavailable'}
        assert runtime.ready()['stored_windows']==0
