"""Verify known PSI answers, boundaries, persistence, concurrent replay and async API failures."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import asyncio
import json
import math
import time
import httpx
import pytest
from hypothesis import given, settings, strategies as st
from fastapi.testclient import TestClient
from services.drift_api.app import create_app
from services.drift_api.contracts import CompareRequest, Observation
from services.drift_api.detector import DriftDetector, canonical_bytes, histogram, population_stability
from services.drift_api.runtime import DriftRuntime, WindowConflict, StoreFull


def feature(index):
    """Use a transparent ten-value population with all eight valid numeric features."""
    return {'temperature_mean_10m': float(index % 10), 'vibration_max_10m': float(index % 10),
            'current_mean_10m': 12.0, 'current_std_10m': 1.0, 'rpm_mean_10m': 3000.0,
            'reading_count_10m': 10, 'machine_age_days': 180.0, 'prior_failures_24h': 0}


def bundle():
    """Freeze a uniform100row population and an explicit non-statistical alert policy."""
    return {'format': 'iot-drift-reference-v1', 'lineage': {'fixture': 'known-ten-values'},
            'policy': {'bins': 10, 'psi_threshold': 0.2, 'drifted_feature_fraction': 0.25, 'pseudocount': 0.5},
            'samples': [feature(i) for i in range(100)]}


def window(reference=None, name='normal'):
    """Keep every observation available before the declared UTC window end."""
    return {'reference_id': reference or DriftDetector(bundle()).reference_id, 'window_id': name,
            'start': '2026-10-01T00:00:00Z', 'end': '2026-10-01T01:00:00Z',
            'observations': [{'id': f'row-{i}', 'event_timestamp': '2026-10-01T00:30:00Z',
                              'available_at': '2026-10-01T00:31:00Z', 'features': feature(i)} for i in range(100)]}


@pytest.fixture
def runtime(tmp_path):
    """Use a genuine temporary SQLite database, retaining each test's independent reference."""
    return DriftRuntime(bundle(), tmp_path/'windows.db')


def test_histogram_boundary_and_psi_hand_answer():
    assert histogram([-100, 0, 0.5, 1, 100], [0, 1]) == [1, 2, 2]
    assert population_stability([10, 0], [0, 10]) == pytest.approx(20/11*math.log(21))
    assert population_stability([10, 10], [10, 10]) == 0
    assert population_stability([2, 7], [9, 0]) == pytest.approx(population_stability([9, 0], [2, 7]), abs=1e-14)


def test_canonical_identity_has_a_stable_external_encoding():
    """A published reference ID must survive dictionary insertion order and reject nonfinite metadata."""
    expected=b'{"a":{"b":1},"z":[1,2]}'
    assert canonical_bytes({'z':[1,2],'a':{'b':1}})==expected
    assert canonical_bytes({'a':{'b':1},'z':[1,2]})==expected
    for invalid in [float('nan'),float('inf'),float('-inf')]:
        with pytest.raises(ValueError):canonical_bytes({'metadata':invalid})
    normalized=CompareRequest.model_validate(window()).canonical()
    for row in normalized['observations']:
        parsed=Observation.model_validate(row)
        assert parsed.id.startswith('row-') and parsed.event_timestamp.isoformat()=='2026-10-01T00:30:00+00:00'
        assert parsed.available_at.isoformat()=='2026-10-01T00:31:00+00:00'


@pytest.mark.parametrize('rows,valid',[(31,False),(32,True),(2048,True),(2049,False)])
def test_reference_sample_boundaries(rows,valid):
    value=bundle();value['samples']=[feature(i) for i in range(rows)]
    if valid:assert DriftDetector(value).rows==rows
    else:
        with pytest.raises(ValueError):DriftDetector(value)


def test_new_nested_state_directory_is_usable(tmp_path):
    value=DriftRuntime(bundle(),tmp_path/'new'/'nested'/'state.db')
    assert value.ready()['stored_windows']==0
    value.compare(CompareRequest.model_validate(window()))
    assert value.ready()['stored_windows']==1


def test_normal_shift_constant_feature_and_complete_lineage(runtime):
    first = runtime.compare(CompareRequest.model_validate(window()))
    assert first['drift_detected'] is False and first['drifted_feature_fraction'] == 0
    assert first['unique_rows'] == first['reference_rows'] == 100 and first['lineage'] == bundle()['lineage']
    assert first['start'] == '2026-10-01T00:00:00+00:00' and first['end'] == '2026-10-01T01:00:00+00:00'
    assert first['reference_id'] == runtime.detector.reference_id and first['window_id'] == 'normal'
    assert first['policy'] == bundle()['policy'] and len(first['features']) == 8
    assert all(v['psi'] == 0 and not v['drifted'] for v in first['features'].values())
    assert first['features']['temperature_mean_10m']['cuts']==list(range(1,10))
    assert first['features']['temperature_mean_10m']['reference_counts']==[10]*10
    assert first['features']['temperature_mean_10m']['current_counts']==[10]*10
    changed = window(name='shift')
    for row in changed['observations']:
        row['features']['temperature_mean_10m'] += 50
        row['features']['vibration_max_10m'] += 50
    result = runtime.compare(CompareRequest.model_validate(changed))
    assert result['drift_detected'] is True and result['drifted_feature_fraction'] == .25
    for name in ['temperature_mean_10m', 'vibration_max_10m']:
        assert result['features'][name]['drifted'] and result['features'][name]['psi'] > 1
        assert result['features'][name]['current_counts'] == [0]*9+[100]
    assert first['features']['current_mean_10m'] == {'psi': 0.0, 'drifted': False, 'cuts': [12.0], 'reference_counts': [0, 100], 'current_counts': [0, 100]}
    assert runtime.ready() == {'ready': True, 'reference_id': result['reference_id'], 'stored_windows': 2}


def test_persistent_replay_conflict_capacity_and_concurrent_workers(tmp_path):
    path = tmp_path/'windows.db';r = DriftRuntime(bundle(), path, max_windows=1)
    request = CompareRequest.model_validate(window())
    with ThreadPoolExecutor(max_workers=6) as pool:
        values = list(pool.map(lambda _: DriftRuntime(bundle(), path, max_windows=1).compare(request), range(12)))
    assert all(item == values[0] for item in values) and r.ready()['stored_windows'] == 1
    assert DriftRuntime(bundle(), path, max_windows=1).compare(request) == values[0]
    changed = window();changed['observations'][0]['features']['current_mean_10m'] += 1
    with pytest.raises(WindowConflict): r.compare(CompareRequest.model_validate(changed))
    bad = window(reference='a'*64)
    with pytest.raises(WindowConflict): r.compare(CompareRequest.model_validate(bad))
    with pytest.raises(StoreFull): r.compare(CompareRequest.model_validate(window(name='second')))
    assert r.ready()['stored_windows'] == 1


@given(st.permutations(tuple(range(10))), st.integers(min_value=0, max_value=10))
@settings(max_examples=35, deadline=None)
def test_property_row_order_duplicate_and_timezone_invariance(order, repeats):
    left = window();right = deepcopy(left)
    right['observations'] = [right['observations'][i] for i in order] + right['observations'][10:]
    right['observations'] += deepcopy(right['observations'][:repeats])
    right['start'] = '2026-10-01T07:00:00+07:00'
    assert CompareRequest.model_validate(left).canonical() == CompareRequest.model_validate(right).canonical()


@pytest.mark.parametrize('size,valid', [(31, False), (32, True), (2048, True), (2049, False)])
def test_sample_size_partitions(size, valid, runtime):
    body = window();body['observations'] = [{**deepcopy(body['observations'][0]), 'id': f'x-{i}'} for i in range(size)]
    with TestClient(create_app(runtime)) as client:
        assert client.post('/v1/drift:compare', json=body).status_code == (200 if valid else 422)


@pytest.mark.parametrize('change', [
    lambda b: b.update(end=b['start']),
    lambda b: b['observations'][0].update(event_timestamp=b['end']),
    lambda b: b['observations'][0].update(event_timestamp='2026-09-30T23:59:59Z'),
    lambda b: b['observations'][0].update(available_at='2026-10-01T01:00:01Z'),
    lambda b: b['observations'][0].update(available_at='2026-10-01T00:29:59Z'),
    lambda b: b.update(start='2026-10-01T00:00:00'),
    lambda b: b['observations'][0]['features'].update(label=1),
    lambda b: b['observations'][0]['features'].update(temperature_mean_10m='12'),
    lambda b: b['observations'][0]['features'].update(reading_count_10m=0),
    lambda b: b['observations'][0].update(id='../bad'),
    lambda b: b.update(observations=b['observations'][:1]*100),
    lambda b: b['observations'].append({**b['observations'][0], 'features': feature(7)}),
])
def test_invalid_window_equivalence_partitions(change, runtime):
    body = window();change(body)
    with TestClient(create_app(runtime)) as client:
        assert client.post('/v1/drift:compare', json=body).status_code == 422
    assert runtime.ready()['stored_windows'] == 0


def test_inclusive_start_availability_end_and_http_error_paths(runtime, tmp_path):
    body = window();body['observations'][0].update(event_timestamp=body['start'], available_at=body['end'])
    with TestClient(create_app(runtime)) as client:
        assert client.get('/health/live').json() == {'live': True}
        assert client.get('/health/ready').json()['ready']
        assert client.post('/v1/drift:compare', json=body).status_code == 200
        mismatch=client.post('/v1/drift:compare', json=window(reference='b'*64))
        assert mismatch.status_code==409 and 'reference' in mismatch.json()['detail'].lower()
        changed=deepcopy(body);changed['observations'][0]['features']['current_mean_10m']+=1
        conflict=client.post('/v1/drift:compare',json=changed)
        assert conflict.status_code==409 and 'different data' in conflict.json()['detail'].lower()
        assert client.post('/v1/drift:compare', content=b'x', headers={'content-length':'1048577'}).status_code == 413
        assert client.post('/v1/drift:compare', content=iter([b'x'])).status_code == 411
        client.app.state.runtime = None
        unavailable=client.get('/health/ready')
        assert unavailable.status_code==503 and 'not ready' in unavailable.json()['detail'].lower()
    full = DriftRuntime(bundle(), tmp_path/'full.db', max_windows=1)
    full.compare(CompareRequest.model_validate(window()))
    with TestClient(create_app(full)) as client:
        capacity=client.post('/v1/drift:compare', json=window(name='two'))
        assert capacity.status_code==507 and 'capacity' in capacity.json()['detail'].lower()


def test_unavailable_store_and_default_environment(monkeypatch, tmp_path):
    source=tmp_path/'reference.json';source.write_text(json.dumps(bundle()));db=tmp_path/'db'
    monkeypatch.setenv('IOT_DRIFT_REFERENCE', str(source));monkeypatch.setenv('IOT_DRIFT_DATABASE', str(db))
    with TestClient(create_app()) as client:
        assert client.get('/health/ready').json()['reference_id'] == sha256(canonical_bytes(bundle())).hexdigest()
        db.unlink();db.mkdir()
        failure=client.get('/health/ready')
        assert failure.status_code==503 and 'unavailable' in failure.json()['detail'].lower()


@pytest.mark.skipif(not hasattr(time,'tzset'),reason='Native POSIX timezone probe; Windows has no tzset')
def test_canonical_times_ignore_server_timezone(monkeypatch):
    import os
    prior=os.environ.get('TZ')
    try:
        monkeypatch.setenv('TZ','Asia/Ho_Chi_Minh');time.tzset()
        normalized=CompareRequest.model_validate(window()).canonical()
        assert normalized['start']=='2026-10-01T00:00:00+00:00'
        assert normalized['observations'][0]['available_at']=='2026-10-01T00:31:00+00:00'
    finally:
        if prior is None:monkeypatch.delenv('TZ',raising=False)
        else:monkeypatch.setenv('TZ',prior)
        time.tzset()


def test_async_health_remains_available_during_blocking_assessment():
    class Slow:
        """Block a worker briefly to detect accidentally blocking the ASGI event loop."""
        def compare(self, request):time.sleep(.15);return {'done': True}
    async def check():
        app = create_app(Slow())
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            pending = asyncio.create_task(client.post('/v1/drift:compare', json=window()))
            await asyncio.sleep(.02)
            result = await client.get('/health/live')
            assert result.status_code == 200 and not pending.done()
            assert (await pending).json() == {'done': True}
    asyncio.run(check())


@pytest.mark.parametrize('change', [lambda b:b.update(format='wrong'),lambda b:b.update(extra=1),
    lambda b:b['policy'].update(psi_threshold=.1),lambda b:b.update(samples=b['samples'][:31]),
    lambda b:b.update(samples=b['samples']*21),lambda b:b['samples'][0].update(current_mean_10m=float('inf'))])
def test_corrupt_reference_is_rejected(change):
    value=bundle();change(value)
    with pytest.raises(ValueError):DriftDetector(value)


@pytest.mark.parametrize('capacity', [0, -1, True, 1.5])
def test_invalid_capacity_fails_closed(tmp_path, capacity):
    with pytest.raises(ValueError):DriftRuntime(bundle(), tmp_path/'db', max_windows=capacity)
