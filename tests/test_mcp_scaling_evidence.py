"""Reject replica-only scaling claims that lack ready serving Pods and healthy demand."""
from copy import deepcopy
import pytest
from infra.mcp_scaling import evaluate
import infra.mcp_scaling as scaling


def sample(phase, count, utilization=5):
    """Represent a controller observation independently of the production recorder."""
    return {'phase': phase, 'roles': {role: {
        'deployment_uid': role+'-deployment', 'template_sha256': 'a'*64,
        'desired': count, 'hpa_uid': role+'-hpa', 'hpa_active': True,
        'demand_value': utilization, 'demand_target': 50, 'metric_source': 'Resource:cpu',
        'pods': [{'uid': role+str(i), 'ready': True, 'successful_calls': 8, 'failed_calls': 0}
                 for i in range(count)]} for role in ['retrieval', 'drift']}}


def test_requires_real_ready_new_pods_serving_under_cpu_demand_and_return():
    """Accept both roles only after baseline, demand-driven serving growth and automatic return."""
    rows = [sample('baseline', 2), sample('load', 4, 120), sample('cooldown', 2)]
    proof = evaluate(rows, {'retrieval': {'success': 20, 'failure': 0}, 'drift': {'success': 10, 'failure': 0}})
    assert proof['passed'] and set(proof['roles']) == {'retrieval', 'drift'}
    assert all(len(v['new_serving_pod_uids']) == 2 for v in proof['roles'].values())


@pytest.mark.parametrize('fault', ['not-ready', 'no-calls', 'no-demand', 'no-return', 'rollout', 'metric-failure', 'request-failure'])
def test_rejects_incomplete_or_misattributed_scaling(fault):
    """A new replica, rollout, fallback or failed request cannot substitute for a completed scale cycle."""
    rows = [sample('baseline', 2), sample('load', 4, 120), sample('cooldown', 2)]
    counts = {'retrieval': {'success': 20, 'failure': 0}, 'drift': {'success': 10, 'failure': 0}}
    peak = rows[1]['roles']['drift']
    if fault == 'not-ready': peak['pods'][-1]['ready'] = False
    if fault == 'no-calls': peak['pods'][-1]['successful_calls'] = 0
    if fault == 'no-demand': peak['demand_value'] = 5
    if fault == 'no-return': rows[-1] = deepcopy(rows[1]); rows[-1]['phase'] = 'cooldown'
    if fault == 'rollout': peak['template_sha256'] = 'b'*64
    if fault == 'metric-failure': peak['hpa_active'] = False
    if fault == 'request-failure': counts['drift']['failure'] = 1
    with pytest.raises(ValueError): evaluate(rows, counts)


def test_external_hpa_milli_quantities_preserve_request_rate_units():
    """Native Kubernetes quantities express fractional requests/s with milli, micro or nano suffixes."""
    assert callable(getattr(scaling, 'quantity', None))
    for raw, expected in [('500m', .5), ('250000u', .25), ('100000000n', .1), ('2', 2), ('1.5', 1.5)]:
        assert scaling.quantity(raw) == expected
    for raw in ['1Gi', 'nan', 'inf', '-1']:
        with pytest.raises(ValueError): scaling.quantity(raw)
