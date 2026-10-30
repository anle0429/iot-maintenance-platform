"""Exercise exact time, prediction and drift decision boundaries with independently specified outcomes."""
from datetime import datetime, timedelta, timezone
import asyncio
import pytest

pytest.importorskip('mcp', reason='Use the dedicated MCP test environment')
import httpx2
from pydantic import ValidationError
from services.mcp_tools.contracts import MachineData, PredictionData, DriftRequest
from services.mcp_tools.runtime import ToolRuntime
from services.predictor.contracts import FeatureVector
from services.drift_api.contracts import CompareRequest
from services.drift_api.runtime import DriftRuntime
from test_tools import machine, prediction, BUNDLE, VALUES


@pytest.mark.parametrize('actual_age,reported_age,accepted', [
    (0, 0, True), (600, 600, True), (-0.000001, 0, False), (600.000001, 600, False),
    (0, 0.001, True), (0, 0.001001, False),
])
def test_actual_feature_time_boundaries_are_independent_of_reported_rounding(actual_age, reported_age, accepted):
    """A rounded age field cannot excuse a future/stale timestamp, while both exact valid edges remain accepted."""
    row = machine(); now = datetime(2026, 10, 1, 0, 10, tzinfo=timezone.utc)
    row['as_of'] = now.isoformat()
    row['feature_event_ts'] = (now - timedelta(seconds=actual_age)).isoformat()
    row['feature_age_seconds'] = reported_age
    row['operating_context'].update(event_ts=now.isoformat(), received_ts=now.isoformat())
    if accepted:
        assert MachineData.model_validate(row).feature_age_seconds == reported_age
    else:
        with pytest.raises(ValidationError): MachineData.model_validate(row)


@pytest.mark.parametrize('score,threshold,decision', [
    (0., 0., 1), (.5, .5, 1), (1., 1., 1), (.499999, .5, 0), (.500001, .5, 1), (0., 1., 0), (1., 0., 1),
])
def test_exact_prediction_threshold_and_score_edges(score, threshold, decision):
    """Equality means a positive model decision; zero and one remain valid numeric score/threshold boundaries."""
    row = prediction(); row['prediction'].update(risk_score=score, predicted_failure=decision)
    row['model']['threshold'] = threshold
    assert PredictionData.model_validate(row).prediction['predicted_failure'] == decision
    row['prediction']['predicted_failure'] = 1 - decision
    with pytest.raises(ValidationError): PredictionData.model_validate(row)


@pytest.mark.parametrize('count,expected_detection', [(0, False), (1, False), (2, True), (8, True)])
def test_quarter_feature_drift_boundary_uses_actual_histograms(count, expected_detection, tmp_path):
    """Two of eight truly shifted feature distributions reach the declared 0.25 alarm boundary; one does not."""
    reference = dict(BUNDLE, samples=[{name: value + 10 for name, value in VALUES.items()} for _ in range(32)])
    detector = DriftRuntime(reference, tmp_path / 'quarter-boundary.db')
    reference_id = detector.ready()['reference_id']
    changed = set(list(FeatureVector.model_fields)[:count])

    def handle(request):
        """Shift selected numeric features for every machine while retaining real native histogram calculation."""
        if request.url.path == '/v1/drift:compare':
            return httpx2.Response(200, json=detector.compare(CompareRequest.model_validate_json(request.content)))
        if request.url.path.endswith('/features'):
            row = machine(request.url.path.split('/')[3])
            # Constant reference values occupy the upper bin at their cut; move selected values across that cut.
            row['features'] = {name: value + (9 if name in changed else 10) for name, value in VALUES.items()}
            return httpx2.Response(200, json=row)
        raise AssertionError('Unexpected native route in the boundary fixture')

    async def scenario():
        """Run the actual asynchronous adapter and drift engine with a source-consistent cohort of 32 machines."""
        upstream = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
        runtime = ToolRuntime('drift', reference_id, client=upstream)
        try:
            result = await runtime.drift(DriftRequest(window_id='quarter-boundary', machine_ids=[f'P-{i:03}' for i in range(1, 33)]))
            assert result['unique_rows'] == 32
            assert {name for name, value in result['features'].items() if value['drifted']} == changed
            assert result['drifted_feature_fraction'] == count / 8
            assert result['drift_detected'] is expected_detection
        finally:
            await runtime.close()
    asyncio.run(scenario())
