"""Reject malformed upstream operating timestamps through Pydantic and safe REST errors instead of uncaught exceptions."""
from copy import deepcopy
import pytest

pytest.importorskip('mcp', reason='Use the dedicated MCP test dependencies')
import httpx2
from fastapi.testclient import TestClient
from pydantic import ValidationError
from services.mcp_tools.app import create_app
from services.mcp_tools.contracts import MachineData
from services.mcp_tools.runtime import ToolRuntime
from test_tools import machine, prediction, REFERENCE, TOKEN, HEADERS, SmallKnowledge

INVALID_CLOCKS = [None, 17, True, [], {'private_marker': 'unit-upstream-diagnostic'}, 'missing-field']


def malformed_machine(field, value):
    """Change one explicit native clock partition without introducing unrelated validation failures."""
    row = machine()
    if value == 'missing-field':
        row['operating_context'].pop(field)
    else:
        row['operating_context'][field] = deepcopy(value)
    return row


@pytest.mark.parametrize('route', ['features', 'predict'])
@pytest.mark.parametrize('field', ['event_ts', 'received_ts'])
@pytest.mark.parametrize('value', INVALID_CLOCKS)
def test_invalid_native_clock_has_safe_unavailable_status(route, field, value):
    """A backend 200 with a non-string or missing clock must become 503 in both feature and prediction routes."""
    row = malformed_machine(field, value)
    raw = row if route == 'features' else dict(prediction(), feature_trace=row)
    upstream = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=raw)))
    runtime = ToolRuntime('retrieval', REFERENCE, SmallKnowledge(), upstream)
    with TestClient(create_app(runtime, 'retrieval', TOKEN), raise_server_exceptions=False) as api:
        response = (api.get('/v1/machines/P-001/features', headers=HEADERS) if route == 'features'
                    else api.post('/v1/machines/P-001:predict', headers=HEADERS, json={}))
        assert response.status_code == 503
        assert isinstance(response.json()['detail'], str)
        assert 'unit-upstream-diagnostic' not in response.text


@pytest.mark.parametrize('field', ['event_ts', 'received_ts'])
@pytest.mark.parametrize('value', INVALID_CLOCKS)
def test_invalid_native_clock_is_a_validation_error(field, value):
    """Invalid upstream data must use the schema error channel, including absent keys, rather than Python attribute/key errors."""
    with pytest.raises(ValidationError):
        MachineData.model_validate(malformed_machine(field, value))
