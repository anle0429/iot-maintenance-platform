"""Evaluate measured MCP elasticity separately from replica configuration."""
from math import isfinite


def quantity(value):
    """Read the dimensionless native HPA request-rate quantity without confusing milli-units with whole requests."""
    raw = str(value); factor = {'m': 1e-3, 'u': 1e-6, 'n': 1e-9}.get(raw[-1:], 1)
    result = float(raw[:-1] if factor != 1 else raw)*factor
    if not isfinite(result) or result < 0:
        raise ValueError('Require a finite nonnegative HPA metric')
    return result


def evaluate(samples, requests):
    """Require complete native scale cycles for both role-isolated MCP services."""
    if len(samples) < 3 or samples[0]['phase'] != 'baseline' or samples[-1]['phase'] != 'cooldown':
        raise ValueError('Missing baseline/load/cooldown observations')
    result = {}
    for role in ['retrieval', 'drift']:
        rows = [(sample['phase'], sample['roles'][role]) for sample in samples]
        first, last = rows[0][1], rows[-1][1]
        identity = tuple(first[key] for key in ['deployment_uid', 'template_sha256', 'hpa_uid', 'metric_source'])
        if any(tuple(row[key] for key in ['deployment_uid', 'template_sha256', 'hpa_uid', 'metric_source']) != identity for _, row in rows):
            raise ValueError('Deployment, template or HPA changed during measurement')
        for row in [first, last]:
            if row['desired'] != 2 or sum(p['ready'] for p in row['pods']) != 2 or not row['hpa_active']:
                raise ValueError('Require two ready replicas and healthy metrics at both boundaries')
        baseline = {p['uid'] for p in first['pods']}
        load = [row for phase, row in rows if phase in ['load', 'drain']]
        peaks = [row for row in load if row['desired'] == 4 and sum(p['ready'] for p in row['pods']) == 4 and row['hpa_active']]
        demand = [row for row in load if row['hpa_active'] and row['demand_value'] is not None and row['demand_value'] > row['demand_target']]
        served = {p['uid'] for row in peaks for p in row['pods'] if p['uid'] not in baseline and p['ready'] and p['successful_calls'] > 0}
        if not peaks or not demand or len(served) < 2:
            raise ValueError('Missing healthy demand-driven growth with two new serving Pods: '+role)
        if requests[role]['success'] < 1 or requests[role]['failure'] != 0:
            raise ValueError('Business workload did not complete without errors: '+role)
        result[role] = {'baseline_ready': 2, 'peak_ready': 4, 'final_ready': 2,
                        'new_serving_pod_uids': sorted(served), 'metric_source': first['metric_source'], 'max_demand': max(row['demand_value'] for row in demand),
                        'requests': requests[role]}
    return {'passed': True, 'roles': result,
            'scope': 'Demand-driven MCP Deployment elasticity, ready new-Pod tool execution and observed return to baseline.'}
