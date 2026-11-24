"""Render real MCP Helm resources and check scaling ownership and bounded behavior."""
from pathlib import Path
import runpy
import shutil
import subprocess
import pytest
import yaml
from agents.rag_search_secret import ensure_search_secret
from infra.llm_observer_monitoring import pod_jobs

ROOT = Path(__file__).resolve().parents[1]
RELEASE = 'iot-retrieval-mcp'


def test_search_scrape_plan_reuses_mcp_identity_with_independent_pod_targets():
    """Prometheus discovers each Search container without rewriting the existing MCP jobs."""
    builder = runpy.run_path(str(ROOT/'scripts/render-rag-search-prometheus.py'))['with_search_job']
    jobs = pod_jobs('/private/token', '/private/ca.pem')
    existing = {'scrape_configs': jobs, 'global': {'scrape_interval': '11s'}}
    planned = builder(existing)
    assert len(existing['scrape_configs']) == 4
    assert planned['scrape_configs'][:-1] == jobs and planned['global'] == existing['global']
    job = planned['scrape_configs'][-1]
    assert job['job_name'] == 'iot-rag-search' and job['metrics_path'] == '/metrics'
    discovery, = job['kubernetes_sd_configs']
    original, = jobs[0]['kubernetes_sd_configs']
    assert discovery['authorization'] == original['authorization'] and discovery['tls_config'] == original['tls_config']
    assert discovery['namespaces']['names'] == ['iot-agents']
    assert discovery['selectors'] == [{'role': 'pod', 'label': 'app.kubernetes.io/name=iot-rag-search'}]
    assert {'action': 'keep', 'regex': 'search', 'source_labels': ['__meta_kubernetes_pod_container_name']} in job['relabel_configs']
    assert {'source_labels': ['__meta_kubernetes_pod_label_app_kubernetes_io_instance'], 'target_label': 'service'} in job['relabel_configs']
    assert job['metric_relabel_configs'][0]['regex'] == 'iot_rag_search_.*'
    assert builder(planned) == planned


def test_drift_capacity_rules_are_registered_once():
    """Keep existing rules and register Drift capacity expressions idempotently."""
    builder = runpy.run_path(str(ROOT/'scripts/render-rag-search-prometheus.py'))['with_search_job']
    rules_path = ROOT/'infra/observability/drift-capacity.rules.yaml'
    rules = yaml.safe_load(rules_path.read_bytes())['groups'][0]['rules']
    assert [rule['alert'] for rule in rules] == [
        'IoTDriftAssessmentCapacityHigh', 'IoTDriftAssessmentCapacityUnreadable',
    ]
    host_path = '/etc/prometheus/drift-capacity.rules.yaml'
    original = {'scrape_configs': pod_jobs('/private/token', '/private/ca.pem'), 'rule_files': ['existing.yaml']}
    configured = builder(original, drift_capacity_rules=host_path)
    assert original['rule_files'] == ['existing.yaml']
    assert configured['rule_files'] == ['existing.yaml', host_path]
    assert builder(configured, drift_capacity_rules=host_path) == configured


def render(*options):
    """Render the maintained chart with synthetic public identifiers, without a cluster."""
    helm = shutil.which('helm')
    if not helm:
        pytest.skip('Helm is required to render the actual chart')
    command = [helm, 'template', RELEASE, str(ROOT/'infra/charts/iot-mcp'),
               '--set', 'tokenSecret=fixture-auth', '--set', 'snapshot.directory=/fixture',
               '--set', 'snapshot.sealConfigMap=fixture-seal', *options]
    return list(yaml.safe_load_all(subprocess.check_output(command, text=True)))


def resource(objects, kind, name):
    """Find the named chart object independently of the order Helm renders templates."""
    return next(row for row in objects if row['kind'] == kind and row['metadata']['name'] == name)


def test_default_preserves_fixed_replica_deployment():
    """Existing releases retain their explicitly configured replica count until autoscaling is enabled."""
    objects = render()
    assert not any(row['kind'] == 'HorizontalPodAutoscaler' for row in objects)
    assert resource(objects, 'Deployment', RELEASE)['spec']['replicas'] == 2


def test_scale_recorder_joins_search_service_to_snapshot_owner():
    """The measured retrieval deployment must be the sealed backend, even when rendered names differ."""
    runner = runpy.run_path(str(ROOT/'scripts/benchmark-mcp-scaling.py'))
    objects = render()
    services = [row for row in objects if row['kind'] == 'Service']
    deployments = [row for row in objects if row['kind'] == 'Deployment']
    owner = runner['retrieval_search_deployment'](services, deployments)
    assert owner['metadata']['name'] == RELEASE+'-search'
    assert runner['snapshot_directory'](services, deployments) == Path('/fixture')


def test_enabled_autoscaler_owns_replicas_and_scales_both_roles():
    """HPA must own the scale field and target the actual application Deployment for either role."""
    for role in ['retrieval', 'drift']:
        objects = render('--set', 'autoscaling.enabled=true', '--set', 'role='+role)
        name = RELEASE+'-search' if role == 'retrieval' else RELEASE
        deployment = resource(objects, 'Deployment', name)
        assert 'replicas' not in deployment['spec']
        assert len([row for row in objects if row['kind'] == 'HorizontalPodAutoscaler']) == 1
        hpa = resource(objects, 'HorizontalPodAutoscaler', name)
        assert hpa['apiVersion'] == 'autoscaling/v2'
        spec = hpa['spec']
        assert spec['scaleTargetRef'] == {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'name': name}
        assert (spec['minReplicas'], spec['maxReplicas']) == (2, 4)
        assert spec['metrics'] == [{'type': 'Resource', 'resource': {'name': 'cpu', 'target': {'type': 'Utilization', 'averageUtilization': 50}}}]
        assert spec['behavior']['scaleDown']['stabilizationWindowSeconds'] == 180
        assert spec['behavior']['scaleUp']['policies'] == [{'type': 'Pods', 'value': 1, 'periodSeconds': 30}]


def test_request_rate_mode_creates_one_keda_owner_with_role_scoped_success_metric():
    """Scale the Search worker on arrivals, and retain drift's original throughput scaler."""
    for role in ['retrieval', 'drift']:
        objects = render('--set', 'autoscaling.enabled=true', '--set', 'autoscaling.mode=requests',
                         '--set', 'role='+role, '--set', 'autoscaling.prometheusAddress=http://prometheus.internal:9090',
                         '--set', 'search.requestsPerSecondPerReplica=2.5')
        name = RELEASE+'-search' if role == 'retrieval' else RELEASE
        assert not any(row['kind'] == 'HorizontalPodAutoscaler' for row in objects)
        assert len([row for row in objects if row['kind'] == 'ScaledObject']) == 1
        scaler = resource(objects, 'ScaledObject', name)
        assert scaler['metadata']['labels']['iot.coursework/agent-scaling'] == 'true'
        assert scaler['spec']['scaleTargetRef']['name'] == name
        trigger = scaler['spec']['triggers'][0]
        assert trigger['type'] == 'prometheus' and trigger['metricType'] == 'AverageValue'
        query = trigger['metadata']['query']
        if role == 'retrieval':
            assert resource(objects, 'Deployment', RELEASE)['spec']['replicas'] == 2
            assert 'replicas' not in resource(objects, 'Deployment', name)['spec']
            assert 'iot_rag_search_attempts_total{job="iot-rag-search"' in query and 'result=' not in query
            assert trigger['metadata']['threshold'] == '2.5'
        else:
            assert 'role="drift",tool="assess_feature_drift",result="success"' in query
            assert trigger['metadata']['threshold'] == '10'
        assert 'service="'+name+'"' in query and '[30s]' in query
        assert trigger['metadata']['ignoreNullValues'] == 'false'
        assert 'or vector(0)' not in query


def test_invalid_autoscaling_configuration_fails_before_any_cluster_write():
    """Reject an ownerless mode, reversed bounds and nonpositive demand target at render time."""
    for option in ['autoscaling.mode=typo', 'autoscaling.maxReplicas=1', 'autoscaling.requestsPerSecondPerReplica=0']:
        with pytest.raises(subprocess.CalledProcessError):
            render('--set', 'autoscaling.enabled=true', '--set', option)


def test_search_backend_owns_snapshot_and_has_release_scoped_connectivity():
    """Keep search data, pod selection, credentials and scaling scoped to their owners."""
    search = RELEASE + '-search'
    objects = render('--set', 'search.replicas=3')
    backend = resource(objects, 'Deployment', search)['spec']
    assert backend['replicas'] == 3
    pods = backend['template']['spec']
    assert pods['initContainers'][0]['name'] == 'copy-verified-snapshot'
    assert 'services.rag_search.app:create_app' in pods['containers'][0]['command']
    frontend = resource(objects, 'Deployment', RELEASE)['spec']['template']['spec']
    assert not frontend.get('initContainers')
    front_volumes = {v['name']: v for v in frontend['volumes']}
    back_volumes = {v['name']: v for v in pods['volumes']}
    assert {'models', 'snapshot', 'snapshot-seal'} <= back_volumes.keys() - front_volumes.keys()
    assert 'credential' not in back_volumes
    assert front_volumes['search-credential']['secret'] == back_volumes['search-credential']['secret']
    service = resource(objects, 'Service', search)['spec']
    assert service['type'] == 'ClusterIP'
    selector = {'app.kubernetes.io/instance': search}
    assert service['selector'] == backend['selector']['matchLabels'] == selector
    policy = resource(objects, 'NetworkPolicy', search)['spec']
    assert policy['podSelector']['matchLabels'] == selector
    assert policy['ingress'] == [{'from': [{'podSelector': {'matchLabels': {'app.kubernetes.io/instance': RELEASE}}},
                                          {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'iot-monitoring'}}}],
                                  'ports': [{'protocol': 'TCP', 'port': 8080}]}]
    assert policy['egress'] == []
    assert {'to': [{'podSelector': {'matchLabels': selector}}],
            'ports': [{'protocol': 'TCP', 'port': 8080}]} in resource(objects, 'NetworkPolicy', RELEASE)['spec']['egress']
    assert not any(obj['metadata']['name'] == search for obj in render('--set', 'role=drift'))


@pytest.mark.parametrize(('existing', 'create_exit', 'recheck', 'outcome'), [
    (b'valid', None, None, None), (b'invalid', None, None, ValueError),
    (b'', 0, None, None), (b'', 1, b'valid', None), (b'', 1, b'', RuntimeError),
])
def test_search_credential_is_created_once_or_reused(monkeypatch, existing, create_exit, recheck, outcome):
    """Idempotent upgrades share the backend key, including a concurrent Secret creation."""
    import json
    from unittest.mock import Mock
    results = [subprocess.CompletedProcess([], 0, existing)]
    if create_exit is not None:
        results.append(subprocess.CompletedProcess([], create_exit))
    if recheck is not None:
        results.append(subprocess.CompletedProcess([], 0, recheck))
    run = Mock(side_effect=results)
    monkeypatch.setattr(subprocess, 'run', run)
    if outcome:
        with pytest.raises(outcome):
            ensure_search_secret(['kubectl'], 'test', 'rag-auth')
    else:
        ensure_search_secret(['kubectl'], 'test', 'rag-auth')
    assert run.call_count == len(results)
    if create_exit is not None:
        create = run.call_args_list[1]
        body = json.loads(create.kwargs['input'])
        assert body['metadata'] == {'name': 'rag-auth', 'namespace': 'test'}
        assert len(body['stringData']['token']) >= 32
        assert create.args[0] == ['kubectl', '-n', 'test', 'create', '-f', '-']
