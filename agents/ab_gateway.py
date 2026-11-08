"""Plan an in-place migration of the retained A/B pair to the selected private gateway."""
from copy import deepcopy
from hashlib import sha256
import json

from agents.ab_configuration import VARIANTS, configuration_plan


NAMESPACE = 'iot-agents'
GATEWAY = 'iot-agentgateway-candidate.iot-agents.svc.cluster.local'
MODEL_URL = 'http://' + GATEWAY + ':18080/v1'
TOOLS_URL = 'http://' + GATEWAY + ':18083/mcp/retrieval'
DIRECT_MODEL_URL = 'http://iot-router-epp.iot-llm.svc.cluster.local:8081/v1'
MODEL_DOMAIN = 'iot-router-epp.iot-llm.svc.cluster.local'
DIRECT_TOOL_DOMAIN = 'iot-retrieval-mcp.iot-agents.svc.cluster.local'
TOOL_NAMES = ['get_machine_features', 'predict_failure', 'search_knowledge']
GATEWAY_TOOL = {'type': 'McpServer', 'mcpServer': {'apiGroup': 'kagent.dev',
    'kind': 'RemoteMCPServer', 'name': 'iot-gateway-retrieval-tools', 'toolNames': TOOL_NAMES}}


def _digest(value):
    """Bind a public resource field to its complete canonical JSON value."""
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _identity(resource, kind, name):
    """Require an observed retained resource rather than an unversioned create manifest."""
    metadata = resource['metadata']
    if (resource.get('apiVersion') != 'kagent.dev/v1alpha2' or resource.get('kind') != kind
            or metadata.get('name') != name or metadata.get('namespace') != NAMESPACE
            or any(not isinstance(metadata.get(key), str) or not metadata[key]
                   for key in ['uid', 'resourceVersion'])):
        raise ValueError('Require the retained namespaced resource UID and resourceVersion')
    return {'kind': kind, 'name': name, 'namespace': NAMESPACE,
            'uid': metadata['uid'], 'resource_version': metadata['resourceVersion']}


def _resources(items, kind, names):
    """Select the complete known resource cohort without ignoring duplicates or unknown agents."""
    if not isinstance(items, list) or len(items) != len(names):
        raise ValueError('Require the complete retained resource cohort')
    selected = {}
    for resource in items:
        name = resource['metadata']['name']
        if name not in names or name in selected:
            raise ValueError('Unknown or duplicate retained resource')
        _identity(resource, kind, name)
        selected[name] = resource
    return selected


def _require_selected_dependencies(model, retrieval, tool, policy):
    """Require the already selected model/MCP route and unchanged readonly retrieval contract."""
    if model['spec'] != {'apiKeySecret': 'iot-local-model-client', 'apiKeySecretKey': 'API_KEY',
            'model': 'iot-qwen2.5-7b', 'provider': 'OpenAI', 'openAI': {
                'apiFormat': 'chatCompletions', 'baseUrl': MODEL_URL, 'maxTokens': 768,
                'seed': 7, 'temperature': '0', 'timeout': 600}}:
        raise ValueError('Require the reviewed shared 7B gateway ModelConfig')
    spec = retrieval['spec']
    if (spec['type'] != 'Declarative' or spec['version'] != '0.2.2' or policy['version'] != '0.2.2'
            or spec['allowedNamespaces'] != {'from': 'Same'}
            or spec['sandbox'] != {'network': {'allowedDomains': [MODEL_DOMAIN, GATEWAY]}}
            or spec['substrate'] != {'workerPoolRef': {'name': 'iot-retrieval-pool'}}
            or spec['declarative'] != {'executeCodeBlocks': False, 'modelConfig': 'default-model-config',
                'runtime': 'go', 'stream': True, 'systemMessage': policy['prompts']['retrieval'],
                'tools': [GATEWAY_TOOL]}):
        raise ValueError('Require the selected readonly retrieval gateway profile')
    _identity(tool, 'RemoteMCPServer', 'iot-gateway-retrieval-tools')
    expected_tool_spec = {'allowedNamespaces': {'from': 'Same'},
        'description': tool['spec']['description'],
        'headersFrom': [{'name': 'Authorization', 'valueFrom': {'type': 'Secret',
            'name': 'iot-agent-tool-headers', 'key': 'retrieval'}}],
        'protocol': 'STREAMABLE_HTTP', 'terminateOnClose': True, 'timeout': '30s', 'url': TOOLS_URL}
    status = tool['status']
    if (tool['spec'] != expected_tool_spec
            or status.get('observedGeneration') != tool['metadata']['generation']
            or not any(c.get('type') == 'Accepted' and c.get('status') == 'True'
                       for c in status.get('conditions', []))
            or sorted(t['name'] for t in status.get('discoveredTools', [])) != sorted(TOOL_NAMES)):
        raise ValueError('Require the accepted selected gateway MCP alias and exactly its three readonly tools')


def _guarded_patch(resource, replacement):
    """Bind the only changed spec to its UID, resourceVersion and complete prior spec."""
    identity = _identity(resource, resource['kind'], resource['metadata']['name'])
    return {**identity, 'before_sha256': _digest(resource['spec']), 'after_sha256': _digest(replacement),
        'operations': [
            {'op': 'test', 'path': '/metadata/uid', 'value': identity['uid']},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': identity['resource_version']},
            {'op': 'test', 'path': '/spec', 'value': deepcopy(resource['spec'])},
            {'op': 'replace', 'path': '/spec', 'value': deepcopy(replacement)},
        ]}


def migration_plan(agents, models, gateway_tool, policy):
    """Return four bounded retained-resource patches, or a fresh-guard resume/no-op plan; never access a cluster."""
    try:
        return _migration_plan(agents, models, gateway_tool, policy)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('Malformed or incomplete A/B migration readback') from error


def _migration_plan(agents, models, gateway_tool, policy):
    """Accept only exact original or selected specs, preserving identities and experiment settings."""
    agent_names = {'iot-retrieval', 'iot-drift', 'iot-coordinator'} | {v['agent'] for v in VARIANTS.values()}
    model_names = {'default-model-config'} | {v['model_config'] for v in VARIANTS.values()}
    by_agent = _resources(agents, 'SandboxAgent', agent_names)
    by_model = _resources(models, 'ModelConfig', model_names)
    shared, retrieval = by_model['default-model-config'], by_agent['iot-retrieval']
    _require_selected_dependencies(shared, retrieval, gateway_tool, policy)
    desired = configuration_plan(shared, retrieval, policy)
    patches, targets = [], []
    for item in desired:
        name, kind = item['metadata']['name'], item['kind']
        observed = (by_model if kind == 'ModelConfig' else by_agent)[name]
        variant = next(v for v, settings in VARIANTS.items()
                       if name in (settings['agent'], settings['model_config']))
        labels = observed['metadata'].get('labels', {})
        if any(labels.get(key) != value for key, value in item['metadata']['labels'].items()):
            raise ValueError('Require original experiment and variant labels')
        previous = deepcopy(item['spec'])
        if kind == 'ModelConfig':
            previous['openAI']['baseUrl'] = DIRECT_MODEL_URL
        else:
            previous['declarative']['tools'][0]['mcpServer']['name'] = 'iot-retrieval-tools'
            previous['sandbox']['network']['allowedDomains'] = [MODEL_DOMAIN, DIRECT_TOOL_DOMAIN]
        if observed['spec'] not in (previous, item['spec']):
            raise ValueError('Variant spec differs from both reviewed original and gateway profiles')
        identity = _identity(observed, kind, name)
        targets.append({**identity, 'variant': variant, 'desired_spec': deepcopy(item['spec']),
                        'already_selected': observed['spec'] == item['spec']})
        if observed['spec'] != item['spec']:
            patches.append(_guarded_patch(observed, item['spec']))
    preserved = [by_agent[name] for name in ['iot-retrieval', 'iot-drift', 'iot-coordinator']]
    preserved += [shared, gateway_tool]
    return {'schema_version': 1, 'profile': 'private-agentgateway-ab', 'patches': patches, 'targets': targets,
        'preserved_resources': [{**_identity(v, v['kind'], v['metadata']['name']), 'spec_sha256': _digest(v['spec'])}
                                for v in preserved],
        'scope': 'In-place ModelConfig URL and SandboxAgent MCP/network migration only; no resource creation, '
                 'gateway change, sampling change or automatic variant promotion. Runtime acceptance is separate.'}
