"""Review guarded delegation rollout plans without applying any cluster change."""
from copy import deepcopy
from hashlib import sha256
import importlib
import importlib.util
import json
import unittest

from infra.agentgateway.candidate import IMAGE, NAME, NAMESPACE, PORTS, ROLES, SELECTOR, bearer_hash, config, manifests, peer


RUN = 'agw-delegation-plan'
CONTROLLER = {'app.kubernetes.io/name': 'kagent', 'app.kubernetes.io/component': 'controller'}
HASHES = {role: bearer_hash(role + '-planning-fixture-' + 'a' * 32) for role in PORTS}
DELEGATION_HASH = bearer_hash('delegation-planning-fixture-' + 'b' * 32)


def digest(value):
    """Hash a stable JSON value independently of the planner."""
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def observed(value, number):
    """Attach concrete synthetic Kubernetes identity fields to one readback fixture."""
    result = deepcopy(value)
    result['metadata'].update(uid='fixture-uid-' + str(number), resourceVersion=str(100 + number))
    return result


def fixtures(*, isolated_controller=True, already_new=False):
    """Represent the retained gateway and pre-existing worker/controller isolation."""
    gateway = [observed(value, index) for index, value in enumerate(manifests(HASHES, 'agw-original'))]
    if already_new:
        gateway[0]['data']['config.yaml'] = json.dumps(config(HASHES, tracing=True, delegation_hash=DELEGATION_HASH))
    policies = []
    for index, role in enumerate(ROLES, 10):
        egress = [
            {'to': [peer('ate-system', {'app': 'atenet-router'})], 'ports': [{'protocol': 'TCP', 'port': 8080}]},
            {'to': [peer('iot-llm', {'llm-d-router-gateway': 'iot-router-epp'})], 'ports': [{'protocol': 'TCP', 'port': 8081}]},
            {'to': [peer(NAMESPACE, CONTROLLER)], 'ports': [{'protocol': 'TCP', 'port': 8083}]},
        ]
        if role != 'coordinator':
            egress.append({'to': [peer(NAMESPACE, {'app.kubernetes.io/instance': 'iot-' + role + '-mcp'})],
                           'ports': [{'protocol': 'TCP', 'port': 8080}]})
        policies.append(observed({'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
            'metadata': {'name': 'iot-' + role + '-worker-egress', 'namespace': NAMESPACE},
            'spec': {'podSelector': {'matchLabels': {'ate.dev/worker-pool': 'iot-' + role + '-pool'}},
                     'policyTypes': ['Egress'], 'egress': egress}}, index))
    if isolated_controller:
        policies.append(observed({'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
            'metadata': {'name': 'retained-controller-egress', 'namespace': NAMESPACE},
            'spec': {'podSelector': {'matchLabels': CONTROLLER}, 'policyTypes': ['Egress'],
                     'egress': [{'to': [peer('ate-system', {'app': 'atenet-router'})],
                                 'ports': [{'protocol': 'TCP', 'port': 8080}]}]}}, 20))
    return gateway, policies


def apply_guarded_patch(resource, operations):
    """Apply the planner's simple RFC6902 fields and fail a stale precondition."""
    result = deepcopy(resource)
    for operation in operations:
        parts = operation['path'].strip('/').split('/')
        target = result
        for part in parts[:-1]:
            target = target[part]
        key = parts[-1]
        if operation['op'] == 'test':
            if target[key] != operation['value']:
                raise ValueError('Stale readback')
        elif operation['op'] == 'replace':
            target[key] = deepcopy(operation['value'])
        else:
            raise AssertionError('Unexpected patch operation')
    return result


class DelegationPlanTests(unittest.TestCase):
    """Require exact readback guards, retained routing and additions that cannot first-isolate clients."""

    def plan(self, gateway, policies, *, labels=None, run=RUN):
        """Load the pure planning module only after checking that its API exists."""
        self.assertIsNotNone(importlib.util.find_spec('infra.agentgateway.delegation'), 'Delegation planner is missing')
        module = importlib.import_module('infra.agentgateway.delegation')
        return module.plan(gateway, policies, CONTROLLER if labels is None else labels, HASHES, DELEGATION_HASH, run)

    def test_guarded_plan_preserves_inputs_and_only_changes_reviewed_fields(self):
        """Planning changes gateway data, one restart annotation and bounded ingress without mutating input."""
        gateway, policies = fixtures()
        snapshot = deepcopy((gateway, policies))
        result = self.plan(gateway, policies)
        self.assertEqual((gateway, policies), snapshot)
        self.assertEqual(len(result['patches']), 3)
        self.assertEqual({entry['kind'] for entry in result['patches']}, {'ConfigMap', 'Deployment', 'NetworkPolicy'})
        by_kind = {value['kind']: value for value in gateway}
        for entry in result['patches']:
            original = by_kind[entry['kind']]
            self.assertEqual(entry['uid'], original['metadata']['uid'])
            self.assertEqual(entry['resource_version'], original['metadata']['resourceVersion'])
            self.assertEqual(entry['before_sha256'], digest(original[entry['field']]))
            changed = apply_guarded_patch(original, entry['operations'])
            self.assertEqual(entry['after_sha256'], digest(changed[entry['field']]))
            if entry['kind'] == 'ConfigMap':
                self.assertEqual(json.loads(changed['data']['config.yaml']), config(HASHES, tracing=True, delegation_hash=DELEGATION_HASH))
            elif entry['kind'] == 'Deployment':
                before = deepcopy(original['spec']); after = deepcopy(changed['spec'])
                restart = after['template']['metadata']['annotations'].pop('iot.coursework/agentgateway-delegation-run')
                self.assertEqual(restart, RUN)
                if not after['template']['metadata']['annotations']:
                    after['template']['metadata'].pop('annotations')
                self.assertEqual(after, before)
                self.assertEqual(after['template']['spec']['containers'][0]['image'], IMAGE)
            else:
                self.assertEqual(changed['spec']['egress'], original['spec']['egress'])
                self.assertEqual(changed['spec']['podSelector'], original['spec']['podSelector'])
                for old, new in zip(original['spec']['ingress'], changed['spec']['ingress']):
                    self.assertEqual(new['ports'], old['ports'])
                    self.assertTrue(all(value in new['from'] for value in old['from']))
                    self.assertIn(peer(NAMESPACE, {'iot.coursework/agentgateway-probe': RUN}), new['from'])
        service = by_kind['Service']
        self.assertEqual(result['unchanged'][0]['kind'], 'Service')
        self.assertEqual(result['unchanged'][0]['sha256'], digest(service['spec']))
        self.assertEqual(len(result['inventory']['gateway']), 4)
        self.assertEqual(result['inventory']['listener_ports'], list(PORTS.values()))

    def test_egress_additions_are_exact_and_preserve_original_control_model_and_mcp_rules(self):
        """Every additive policy selects an already isolated client and permits only gateway TCP18083."""
        gateway, policies = fixtures()
        snapshot = deepcopy(policies)
        additions = self.plan(gateway, policies)['additions']
        self.assertEqual(len(additions), 4)
        for addition in additions:
            self.assertEqual(addition['metadata']['namespace'], NAMESPACE)
            self.assertTrue(addition['metadata']['name'].startswith(RUN + '-'))
            self.assertEqual(addition['metadata']['annotations']['iot.coursework/agentgateway-delegation-run'], RUN)
            self.assertEqual(addition['spec']['policyTypes'], ['Egress'])
            self.assertEqual(addition['spec']['egress'], [{'to': [peer(NAMESPACE, SELECTOR)],
                'ports': [{'protocol': 'TCP', 'port': 18083}]}])
        selected = [value['spec']['podSelector'] for value in additions]
        self.assertEqual(selected, [{'matchLabels': {'ate.dev/worker-pool': 'iot-' + role + '-pool'}} for role in ROLES]
                         + [{'matchLabels': CONTROLLER}])
        self.assertEqual(policies, snapshot)

    def test_controller_is_never_first_isolated_and_selector_expressions_are_respected(self):
        """An unrestricted or unselected controller gets no new egress selector."""
        gateway, policies = fixtures(isolated_controller=False)
        self.assertEqual(len(self.plan(gateway, policies)['additions']), 3)
        unrelated = observed({'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
            'metadata': {'name': 'other-egress', 'namespace': NAMESPACE},
            'spec': {'podSelector': {'matchExpressions': [{'key': 'app.kubernetes.io/component',
                'operator': 'In', 'values': ['ui']}]}, 'policyTypes': ['Egress'], 'egress': []}}, 25)
        self.assertEqual(len(self.plan(gateway, policies + [unrelated])['additions']), 3)
        unrelated['spec']['podSelector']['matchExpressions'][0]['values'] = ['controller']
        self.assertEqual(len(self.plan(gateway, policies + [unrelated])['additions']), 4)

    def test_missing_or_mismatched_worker_isolation_is_rejected(self):
        """Missing baseline isolation, broad selectors and combined policy types cannot create first isolation."""
        for mutation in ('missing', 'selector', 'policy-types', 'namespace'):
            gateway, policies = fixtures()
            if mutation == 'missing': policies.pop(0)
            elif mutation == 'selector': policies[0]['spec']['podSelector'] = {}
            elif mutation == 'policy-types': policies[0]['spec']['policyTypes'] = ['Ingress', 'Egress']
            else: policies[0]['metadata']['namespace'] = 'other'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.plan(gateway, policies)

    def test_gateway_requires_exact_owned_identities_and_fresh_metadata(self):
        """Missing, duplicate, foreign, unknown and unversioned gateway objects are rejected."""
        for mutation in ('missing', 'duplicate', 'unknown', 'namespace', 'uid', 'resourceVersion', 'label'):
            gateway, policies = fixtures()
            if mutation == 'missing': gateway.pop()
            elif mutation == 'duplicate': gateway.append(deepcopy(gateway[0]))
            elif mutation == 'unknown': gateway[0]['metadata']['name'] = 'other-gateway'
            elif mutation == 'namespace': gateway[0]['metadata']['namespace'] = 'other'
            elif mutation == 'label': gateway[0]['metadata']['labels'] = {}
            else: gateway[0]['metadata'].pop(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.plan(gateway, policies)

    def test_guards_reject_readbacks_changed_after_planning(self):
        """UID, resourceVersion and the entire patched field are tested before replacement."""
        gateway, policies = fixtures()
        entries = self.plan(gateway, policies)['patches']
        by_kind = {value['kind']: value for value in gateway}
        for entry in entries:
            for field in ('uid', 'resourceVersion', entry['field']):
                stale = deepcopy(by_kind[entry['kind']])
                if field in ('uid', 'resourceVersion'): stale['metadata'][field] += '-changed'
                else: stale[field]['unreviewed'] = True
                with self.subTest(kind=entry['kind'], field=field), self.assertRaises(ValueError):
                    apply_guarded_patch(stale, entry['operations'])

    def test_unknown_configuration_and_public_or_changed_immutable_contracts_are_rejected(self):
        """Only source-exact old/new configs, the pinned gateway image and private four-port Service are accepted."""
        for mutation in ('config', 'image', 'selector', 'service-type', 'service-port', 'external-ip', 'host-network'):
            gateway, policies = fixtures()
            by_kind = {value['kind']: value for value in gateway}
            if mutation == 'config':
                value = json.loads(by_kind['ConfigMap']['data']['config.yaml']); value['binds'].pop()
                by_kind['ConfigMap']['data']['config.yaml'] = json.dumps(value)
            elif mutation == 'image': by_kind['Deployment']['spec']['template']['spec']['containers'][0]['image'] = 'other:image'
            elif mutation == 'selector': by_kind['Deployment']['spec']['selector']['matchLabels'] = {'other': 'gateway'}
            elif mutation == 'service-type': by_kind['Service']['spec']['type'] = 'NodePort'
            elif mutation == 'service-port': by_kind['Service']['spec']['ports'][0]['port'] = 443
            elif mutation == 'external-ip': by_kind['Service']['spec']['externalIPs'] = ['203.0.113.1']
            else: by_kind['Deployment']['spec']['template']['spec']['hostNetwork'] = True
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.plan(gateway, policies)

    def test_already_new_config_is_supported_without_duplicate_ingress_peers(self):
        """A retry accepts its exact new config and does not accumulate ingress entries."""
        gateway, policies = fixtures(already_new=True)
        first = self.plan(gateway, policies)
        by_kind = {value['kind']: index for index, value in enumerate(gateway)}
        for entry in first['patches']:
            index = by_kind[entry['kind']]
            gateway[index] = apply_guarded_patch(gateway[index], entry['operations'])
        second = self.plan(gateway, policies)
        self.assertEqual(first['patches'][2]['after_sha256'], second['patches'][2]['after_sha256'])
        self.assertTrue(all('Authorization' not in json.dumps(item) for item in first['additions']))

    def test_invalid_run_controller_or_policy_selector_is_rejected(self):
        """Malformed ownership, controller identity or selector operators are not treated as safe matches."""
        gateway, policies = fixtures()
        for run in ('bad', 'agw-' + 'a' * 80):
            with self.subTest(run=run), self.assertRaises(ValueError): self.plan(gateway, policies, run=run)
        with self.assertRaises(ValueError): self.plan(gateway, policies, labels={'app.kubernetes.io/name': 'other'})
        policies[-1]['spec']['podSelector'] = {'matchExpressions': [{'key': 'app', 'operator': 'Unknown'}]}
        with self.assertRaises(ValueError): self.plan(gateway, policies)


if __name__ == '__main__':
    unittest.main()
