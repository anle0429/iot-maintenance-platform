"""Check empty acknowledgement, transactional replay and rejection before a drift event is stored."""
from copy import deepcopy
from fastapi.testclient import TestClient
import pytest
from services.drift_api.app import create_app
from services.drift_api.runtime import DriftRuntime
from tests.api.test_drift_api import bundle,window


def headers():
    """Use the publisher's declared binary binding independently of its implementation."""
    return {'ce-specversion':'1.0','ce-type':'dev.edai.iot.drift.window',
            'ce-source':'urn:edai:iot:window-publisher','ce-id':'transport-1','content-type':'application/json'}


def test_empty_ack_after_persist_and_distinct_transport_ids_replay_one_window(tmp_path):
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
    runtime=DriftRuntime(bundle(),tmp_path/'windows.db')
    with TestClient(create_app(runtime)) as client:
        reply=client.post('/v1/events/drift',content=__import__('json').dumps(window()),headers={**headers(),name:value})
        assert reply.status_code in [400,422]
        assert runtime.ready()['stored_windows']==0


def test_metadata_boundaries_and_media_type_parameters(tmp_path):
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
