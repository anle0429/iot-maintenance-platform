"""Exercise the online drift boundary with model-bound features and a fixed reference."""
import asyncio
from copy import deepcopy

import pytest

from services.mcp_tools.contracts import DriftRequest
from services.mcp_tools.runtime import ToolFailure, ToolRuntime
from test_tools import machine, BUNDLE
from services.drift_api.contracts import CompareRequest
from services.drift_api.runtime import DriftRuntime


MODEL = {'name': 'iot-pump-failure-v1', 'version': '7', 'run_id': 'a' * 32,
         'data_version': 'b' * 64, 'protocol_sha256': 'a' * 64, 'production': True}
LINEAGE = {'model_name': MODEL['name'], 'model_version': MODEL['version'], 'model_run_id': MODEL['run_id'],
           'data_version': MODEL['data_version'], 'protocol_sha256': MODEL['protocol_sha256'], 'partition': 'train'}


@pytest.mark.parametrize('fault,expected_status', [(None, 200), ('wrong_reference', 409),
                                                  ('mixed_rollout', 409), ('too_few', 422),
                                                  ('candidate_pin', 503)])
def test_serving_drift_uses_only_operating_rows_and_matching_training_reference(tmp_path, monkeypatch, fault, expected_status):
    """Check eligibility and model provenance before any persistent assessment."""
    current = deepcopy(BUNDLE)
    current['lineage'] = dict(LINEAGE, model_version='3') if fault == 'wrong_reference' else LINEAGE
    detector = DriftRuntime(current, tmp_path / 'drift.sqlite')
    ref = detector.detector.reference_id
    runtime = ToolRuntime('drift', ref, client=object(), drift_scope='serving_model')
    received = []

    async def call(base, path, body=None, **kwargs):
        if base == runtime.DRIFT_URL:
            if path == '/v1/drift/reference':
                return {'reference_id': ref, 'lineage': current['lineage']}
            received.extend(row['id'] for row in body['observations'])
            return detector.compare(CompareRequest.model_validate(body))
        assert path.endswith('/drift-input')
        identifier = path.split('/')[3]
        model = dict(MODEL, version='8') if fault == 'mixed_rollout' and identifier == 'P-032' else MODEL
        if fault == 'candidate_pin' and identifier == 'P-033':
            model = dict(MODEL, production=False)
        return {'feature': machine(identifier), 'serving_model': model}

    async def immediate(*coroutines):
        return [await coroutine for coroutine in coroutines]

    monkeypatch.setattr(runtime, 'call', call)
    monkeypatch.setattr(asyncio, 'gather', immediate)
    population = 32 if fault == 'too_few' else 33
    operation = runtime.drift(DriftRequest(window_id='review', machine_ids=[f'P-{i:03}' for i in range(1, population + 1)]))
    if expected_status == 200:
        with pytest.raises(StopIteration) as completed:
            operation.send(None)
        assert completed.value.value['unique_rows'] == len(received) == 32
        assert 'P-006' not in received
    else:
        with pytest.raises(ToolFailure) as rejected:
            operation.send(None)
        assert rejected.value.status == expected_status
        assert received == []
