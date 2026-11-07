"""Render real MCP Helm resources and check scaling ownership and bounded behavior."""
from pathlib import Path
import shutil
import subprocess
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def render(*options):
    """Render the maintained chart with synthetic public identifiers, without a cluster."""
    helm = shutil.which('helm')
    if not helm:
        pytest.skip('Helm is required to render the actual chart')
    command = [helm, 'template', 'iot-retrieval-mcp', str(ROOT/'infra/charts/iot-mcp'),
               '--set', 'tokenSecret=fixture-auth', '--set', 'snapshot.directory=/fixture',
               '--set', 'snapshot.sealConfigMap=fixture-seal', *options]
    return list(yaml.safe_load_all(subprocess.check_output(command, text=True)))


def test_default_preserves_fixed_replica_deployment():
    """Existing releases retain their explicitly configured replica count until autoscaling is enabled."""
    objects = render()
    assert not any(row['kind'] == 'HorizontalPodAutoscaler' for row in objects)
    assert next(row for row in objects if row['kind'] == 'Deployment')['spec']['replicas'] == 2


def test_enabled_autoscaler_owns_replicas_and_scales_both_roles():
    """HPA must own the scale field and target the actual application Deployment for either role."""
    for role in ['retrieval', 'drift']:
        objects = render('--set', 'autoscaling.enabled=true', '--set', 'role='+role)
        deployment = next(row for row in objects if row['kind'] == 'Deployment')
        assert 'replicas' not in deployment['spec']
        hpa = next(row for row in objects if row['kind'] == 'HorizontalPodAutoscaler')
        assert hpa['apiVersion'] == 'autoscaling/v2'
        spec = hpa['spec']
        assert spec['scaleTargetRef'] == {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'name': 'iot-retrieval-mcp'}
        assert (spec['minReplicas'], spec['maxReplicas']) == (2, 4)
        assert spec['metrics'] == [{'type': 'Resource', 'resource': {'name': 'cpu', 'target': {'type': 'Utilization', 'averageUtilization': 50}}}]
        assert spec['behavior']['scaleDown']['stabilizationWindowSeconds'] == 180
        assert spec['behavior']['scaleUp']['policies'] == [{'type': 'Pods', 'value': 1, 'periodSeconds': 30}]


def test_request_rate_mode_creates_one_keda_owner_with_role_scoped_success_metric():
    """I/O-bound MCPs use measured successful tool throughput without a competing CPU HPA."""
    for role, tool in [('retrieval', 'search_knowledge'), ('drift', 'assess_feature_drift')]:
        objects = render('--set', 'autoscaling.enabled=true', '--set', 'autoscaling.mode=requests',
                         '--set', 'role='+role, '--set', 'autoscaling.prometheusAddress=http://prometheus.internal:9090')
        assert not any(row['kind'] == 'HorizontalPodAutoscaler' for row in objects)
        scaler = next(row for row in objects if row['kind'] == 'ScaledObject')
        assert scaler['metadata']['labels']['iot.coursework/agent-scaling'] == 'true'
        assert scaler['spec']['scaleTargetRef']['name'] == 'iot-retrieval-mcp'
        trigger = scaler['spec']['triggers'][0]
        assert trigger['type'] == 'prometheus' and trigger['metricType'] == 'AverageValue'
        query = trigger['metadata']['query']
        assert 'role="'+role+'"' in query and 'tool="'+tool+'"' in query and 'result="success"' in query
        assert 'service="iot-retrieval-mcp"' in query and '[30s]' in query
        assert trigger['metadata']['ignoreNullValues'] == 'false'
        assert 'or vector(0)' not in query


def test_invalid_autoscaling_configuration_fails_before_any_cluster_write():
    """Reject an ownerless mode, reversed bounds and nonpositive demand target at render time."""
    for option in ['autoscaling.mode=typo', 'autoscaling.maxReplicas=1', 'autoscaling.requestsPerSecondPerReplica=0']:
        with pytest.raises(subprocess.CalledProcessError):
            render('--set', 'autoscaling.enabled=true', '--set', option)
