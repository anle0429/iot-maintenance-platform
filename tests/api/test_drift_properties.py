"""Generate real HTTP/SQLite replay cases whose canonical content survives reordering, duplicate delivery and process-state reopening."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from fastapi.testclient import TestClient
from hypothesis import given,settings,strategies as st
from services.drift_api.app import create_app
from services.drift_api.contracts import CompareRequest
from services.drift_api.runtime import DriftRuntime
from tests.api.test_drift_api import bundle,window


@settings(max_examples=35,deadline=None)
@given(st.permutations(tuple(range(32))),st.integers(min_value=0,max_value=8))
def test_http_sqlite_idempotency_under_generated_reordering_and_duplicates(order,repeats):
    """One logical window must yield one stored assessment and the same HTTP body across generated retries and restart."""
    with TemporaryDirectory() as temporary:
        path=Path(temporary)/'windows.db';runtime=DriftRuntime(bundle(),path);left=window(name='property-persistent-window');right=deepcopy(left)
        right['observations']=[right['observations'][i] for i in order]+right['observations'][32:]
        right['observations']+=deepcopy(right['observations'][:repeats]);right['start']='2026-10-01T07:00:00+07:00'
        with TestClient(create_app(runtime)) as client:
            first=client.post('/v1/drift:compare',json=left);retry=client.post('/v1/drift:compare',json=right)
            assert first.status_code==retry.status_code==200 and first.json()==retry.json()
            assert runtime.ready()['stored_windows']==1
        reopened=DriftRuntime(bundle(),path);assert reopened.compare(CompareRequest.model_validate(right))==first.json() and reopened.ready()['stored_windows']==1
