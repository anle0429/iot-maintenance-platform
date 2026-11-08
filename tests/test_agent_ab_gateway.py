"""Exercise retained A/B migration guards without credentials or a Kubernetes cluster."""
from copy import deepcopy
import importlib
import importlib.util
import unittest

from agents.ab_configuration import configuration_plan


GATEWAY = 'iot-agentgateway-candidate.iot-agents.svc.cluster.local'
DIRECT_MODEL = 'http://iot-router-epp.iot-llm.svc.cluster.local:8081/v1'


def observed(kind, name, spec, number, labels=None):
    """Give one public synthetic resource an independently identifiable retained version."""
    return {'apiVersion': 'kagent.dev/v1alpha2', 'kind': kind,
            'metadata': {'name': name, 'namespace': 'iot-agents', 'uid': 'retained-' + str(number),
                         'resourceVersion': str(100 + number), 'generation': 2, 'labels': labels or {}},
            'spec': deepcopy(spec), 'status': {'observedGeneration': 2, 'conditions': [
                {'type': 'Accepted', 'status': 'True'}]}}


def fixtures():
    """Represent the selected production profile and the two original direct-profile experiments."""
    policy = {'version': '0.2.2', 'prompts': {'retrieval': 'Read actual tool results.'}}
    model = observed('ModelConfig', 'default-model-config', {
        'provider': 'OpenAI', 'model': 'iot-qwen2.5-7b', 'apiKeySecret': 'iot-local-model-client',
        'apiKeySecretKey': 'API_KEY', 'openAI': {'apiFormat': 'chatCompletions',
        'baseUrl': 'http://' + GATEWAY + ':18080/v1', 'temperature': '0', 'seed': 7,
        'maxTokens': 768, 'timeout': 600}}, 1)
    retrieval = observed('SandboxAgent', 'iot-retrieval', {'type': 'Declarative', 'version': '0.2.2',
        'description': 'Read simulated pump data.', 'allowedNamespaces': {'from': 'Same'},
        'sandbox': {'network': {'allowedDomains': ['iot-router-epp.iot-llm.svc.cluster.local', GATEWAY]}},
        'substrate': {'workerPoolRef': {'name': 'iot-retrieval-pool'}},
        'declarative': {'runtime': 'go', 'stream': True, 'modelConfig': 'default-model-config',
        'systemMessage': policy['prompts']['retrieval'], 'executeCodeBlocks': False,
        'tools': [{'type': 'McpServer', 'mcpServer': {'name': 'iot-gateway-retrieval-tools',
            'kind': 'RemoteMCPServer', 'apiGroup': 'kagent.dev',
            'toolNames': ['get_machine_features', 'predict_failure', 'search_knowledge']}}]}}, 2)
    agents = [retrieval] + [observed('SandboxAgent', 'iot-' + role, {'unchanged': role}, index)
                            for index, role in enumerate(['drift', 'coordinator'], 3)]
    models = [model]
    for index, resource in enumerate(configuration_plan(model, retrieval, policy), 10):
        value = observed(resource['kind'], resource['metadata']['name'], resource['spec'], index,
                         resource['metadata']['labels'])
        if value['kind'] == 'ModelConfig':
            value['spec']['openAI']['baseUrl'] = DIRECT_MODEL
            models.append(value)
        else:
            value['spec']['declarative']['tools'][0]['mcpServer']['name'] = 'iot-retrieval-tools'
            value['spec']['sandbox']['network']['allowedDomains'][1] = 'iot-retrieval-mcp.iot-agents.svc.cluster.local'
            agents.append(value)
    tool = observed('RemoteMCPServer', 'iot-gateway-retrieval-tools', {
        'allowedNamespaces': {'from': 'Same'}, 'description': 'Selected gateway tools.',
        'headersFrom': [{'name': 'Authorization', 'valueFrom': {'type': 'Secret',
            'name': 'iot-agent-tool-headers', 'key': 'retrieval'}}],
        'protocol': 'STREAMABLE_HTTP', 'terminateOnClose': True, 'timeout': '30s',
        'url': 'http://' + GATEWAY + ':18083/mcp/retrieval'}, 20)
    tool['status']['discoveredTools'] = [{'name': name} for name in
        ['get_machine_features', 'predict_failure', 'search_knowledge']]
    return agents, models, tool, policy


def apply_patch(resource, operations):
    """Apply only the complete-spec RFC6902 operations expected from the guarded planner."""
    value = deepcopy(resource)
    for operation in operations:
        parts = operation['path'].strip('/').split('/')
        parent = value
        for part in parts[:-1]:
            parent = parent[part]
        if operation['op'] == 'test':
            if parent[parts[-1]] != operation['value']:
                raise ValueError('Stale resource')
        elif operation['op'] == 'replace':
            parent[parts[-1]] = deepcopy(operation['value'])
        else:
            raise AssertionError('Unexpected mutation')
    return value


class AgentAbGatewayTests(unittest.TestCase):
    """Allow only the four reviewed retained-resource transitions and fail closed on changed experiments."""

    def plan(self, *args):
        """Report a missing production planner as a behavioral regression rather than an import crash."""
        self.assertIsNotNone(importlib.util.find_spec('agents.ab_gateway'), 'Retained A/B gateway planner is missing')
        return importlib.import_module('agents.ab_gateway').migration_plan(*args)

    def test_migrate_model_and_mcp_routes_preserves_sampling_and_ownership(self):
        """Both actual ModelConfig URLs and tool aliases must migrate, without altering any production resource."""
        agents, models, tool, policy = fixtures()
        originals = deepcopy((agents, models, tool, policy))
        result = self.plan(agents, models, tool, policy)
        self.assertEqual((agents, models, tool, policy), originals)
        self.assertEqual(len(result['patches']), 4)
        by_name = {v['metadata']['name']: v for v in agents + models}
        for patch in result['patches']:
            before = by_name[patch['name']]
            self.assertEqual([v['path'] for v in patch['operations']],
                ['/metadata/uid', '/metadata/resourceVersion', '/spec', '/spec'])
            after = apply_patch(before, patch['operations'])
            expected = deepcopy(before)
            if patch['kind'] == 'ModelConfig':
                expected['spec']['openAI']['baseUrl'] = 'http://' + GATEWAY + ':18080/v1'
            else:
                expected['spec']['declarative']['tools'][0]['mcpServer']['name'] = 'iot-gateway-retrieval-tools'
                expected['spec']['sandbox']['network']['allowedDomains'][1] = GATEWAY
            self.assertEqual(after, expected)
        self.assertEqual({v['name'] for v in result['preserved_resources']},
            {'iot-retrieval', 'iot-drift', 'iot-coordinator', 'default-model-config', 'iot-gateway-retrieval-tools'})

    def test_stale_uid_version_or_full_spec_cannot_be_overwritten(self):
        """The Kubernetes patch must reject replacement resources, concurrent writes and policy edits."""
        values = fixtures()
        result = self.plan(*values)
        resources = {v['metadata']['name']: v for v in values[0] + values[1]}
        for patch in result['patches']:
            for field in ['uid', 'resourceVersion', 'spec']:
                changed = deepcopy(resources[patch['name']])
                if field == 'spec':
                    changed['spec']['unreviewed'] = True
                else:
                    changed['metadata'][field] += '-changed'
                with self.subTest(resource=patch['name'], field=field):
                    with self.assertRaises(ValueError):
                        apply_patch(changed, patch['operations'])

    def test_reject_changed_or_incomplete_variant_contract(self):
        """A different prompt, permission, temperature, token limit or partial pair requires review."""
        mutations = [
            lambda a,m,t,p: a.pop(),
            lambda a,m,t,p: a.append(deepcopy(a[-1])),
            lambda a,m,t,p: m.pop(),
            lambda a,m,t,p: a[-1]['metadata']['labels'].update({'iot.coursework/variant': 'a'}),
            lambda a,m,t,p: a[-1]['spec']['declarative'].update(systemMessage='Different prompt'),
            lambda a,m,t,p: a[-1]['spec']['declarative'].update(executeCodeBlocks=True),
            lambda a,m,t,p: a[-1]['spec']['declarative']['tools'][0]['mcpServer']['toolNames'].append('start_pump'),
            lambda a,m,t,p: a[-1]['spec']['sandbox']['network']['allowedDomains'].append('*'),
            lambda a,m,t,p: a[-1]['spec']['substrate']['workerPoolRef'].update(name='other-pool'),
            lambda a,m,t,p: m[-1]['spec']['openAI'].update(temperature='0.4'),
            lambda a,m,t,p: m[-1]['spec']['openAI'].update(seed=8),
            lambda a,m,t,p: m[-1]['spec']['openAI'].update(maxTokens=2048),
            lambda a,m,t,p: m[-1]['spec']['openAI'].update(baseUrl='https://public.example/v1'),
            lambda a,m,t,p: a[-1]['metadata'].update(namespace='other'),
            lambda a,m,t,p: m[-1]['metadata'].update(uid=''),
        ]
        for index, mutate in enumerate(mutations):
            values = fixtures()
            mutate(*values)
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    self.plan(*values)

    def test_reject_unselected_or_unaccepted_shared_gateway_dependencies(self):
        """Migration cannot silently point experiments at an altered shared model or unaccepted MCP alias."""
        mutations = [
            lambda a,m,t,p: m[0]['spec']['openAI'].update(baseUrl=DIRECT_MODEL),
            lambda a,m,t,p: a[0]['spec']['declarative']['tools'][0]['mcpServer'].update(name='iot-retrieval-tools'),
            lambda a,m,t,p: t['spec'].update(url='http://' + GATEWAY + ':18081/mcp'),
            lambda a,m,t,p: t['spec']['headersFrom'][0]['valueFrom'].update(key='drift'),
            lambda a,m,t,p: t['status'].update(observedGeneration=1),
            lambda a,m,t,p: t['status']['conditions'][0].update(status='False'),
            lambda a,m,t,p: t['status']['discoveredTools'].append({'name': 'start_pump'}),
        ]
        for index, mutate in enumerate(mutations):
            values = fixtures()
            mutate(*values)
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    self.plan(*values)

    def test_resume_partial_and_completed_migrations_without_recreating_resources(self):
        """A partial native apply can resume using fresh guards; a fully migrated readback is a no-op."""
        values = fixtures()
        first = self.plan(*values)
        by_name = {v['metadata']['name']: v for v in values[0] + values[1]}
        for index, patch in enumerate(first['patches']):
            target = by_name[patch['name']]
            target['spec'] = apply_patch(target, patch['operations'])['spec']
            target['metadata']['resourceVersion'] += '1'
            next_plan = self.plan(*values)
            self.assertEqual(len(next_plan['patches']), 3-index)
            self.assertNotIn(patch['name'], {v['name'] for v in next_plan['patches']})
        self.assertEqual(self.plan(*values)['patches'], [])
