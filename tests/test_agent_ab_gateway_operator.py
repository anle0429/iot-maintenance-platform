"""Reject unsealed source, expired sessions and unsafe generated A/B runtime projections."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/migrate-agent-ab-gateway.py'
GATEWAY = 'iot-agentgateway-candidate.iot-agents.svc.cluster.local'


class AgentAbGatewayOperatorTests(unittest.TestCase):
    """Test fail-closed operator boundaries without imitating kubectl or accepting a fake live result."""

    def module(self):
        """Load the public operator only after asserting its presence as a required behavior."""
        self.assertTrue(SCRIPT.is_file(), 'Retained A/B gateway operator is missing')
        spec = importlib.util.spec_from_file_location('migrate_agent_ab_gateway', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_session_requires_an_unexpired_bounded_lease(self):
        """A missing timezone or insufficient finish reserve must stop the operator before any cluster change."""
        module = self.module()
        now = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        value = {'auto_stop_utc': (now + timedelta(hours=2)).isoformat()}
        self.assertEqual(module.require_session(value, now), 7200)
        for deadline in [now, now + timedelta(minutes=30), now + timedelta(hours=9), now.replace(tzinfo=None)]:
            with self.subTest(deadline=deadline):
                with self.assertRaises(ValueError):
                    module.require_session({'auto_stop_utc': deadline.isoformat()}, now)

    def test_only_selected_url_temperature_and_tool_contract_can_be_projected(self):
        """Generated Secret credentials never enter the projection, and direct routes cannot pass as gateway proof."""
        module = self.module()
        config = {'model': {'type': 'openai', 'model': 'iot-qwen2.5-7b',
            'base_url': 'http://' + GATEWAY + ':18080/v1', 'temperature': 0.3,
            'seed': 7, 'max_tokens': 768, 'headers': {'Authorization': 'Bearer credential-that-must-not-appear'}},
            'http_tools': [{'tools': ['get_machine_features', 'predict_failure', 'search_knowledge'],
                'params': {'url': 'http://' + GATEWAY + ':18083/mcp/retrieval',
                    'headers': {'Authorization': 'Bearer other-private-credential', 'x-kagent-host': GATEWAY}}}]}
        result = module.compiled_projection(config, '0.3')
        self.assertEqual(result['model']['temperature'], 0.3)
        self.assertEqual(result['mcp']['url'], 'http://' + GATEWAY + ':18083/mcp/retrieval')
        self.assertNotIn('credential', json.dumps(result))
        mutations = [
            lambda c: c['model'].update(base_url='http://iot-router-epp.iot-llm.svc.cluster.local:8081/v1'),
            lambda c: c['model'].update(temperature=0),
            lambda c: c['model'].update(seed=8),
            lambda c: c['model'].update(max_tokens=2048),
            lambda c: c['http_tools'][0]['params'].update(url='http://' + GATEWAY + ':18083/mcp'),
            lambda c: c['http_tools'][0]['tools'].append('start_pump'),
            lambda c: c['http_tools'][0]['params']['headers'].update({'x-kagent-host': 'other-host'}),
            lambda c: c.update(remote_agents=[{'url': 'http://unreviewed'}]),
        ]
        for index, mutate in enumerate(mutations):
            changed = deepcopy(config)
            mutate(changed)
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    module.compiled_projection(changed, '0.3')

    def test_source_seal_rejects_modified_missing_or_escaping_members(self):
        """The executed planner and policy must be the exact committed sealed packet, without paths outside its tree."""
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary) / 'tree'
            tree.mkdir()
            files = {}
            for relative in module.REQUIRED_SOURCE:
                path = tree / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('sealed source ' + relative, encoding='utf-8')
                files[relative] = sha256(path.read_bytes()).hexdigest()
            manifest = tree.parent / 'manifest.json'
            original = {'format': 'iot-agent-ab-gateway-source-v1', 'commit': 'a' * 40, 'files': files}
            manifest.write_text(json.dumps(original))
            self.assertEqual(module.source_manifest(tree, manifest), original)
            first = tree / next(iter(files))
            first.write_text('modified source')
            with self.assertRaises(ValueError): module.source_manifest(tree, manifest)
            files[next(iter(files))] = sha256(first.read_bytes()).hexdigest()
            original['files']['../outside'] = 'b' * 64
            manifest.write_text(json.dumps(original))
            with self.assertRaises(ValueError): module.source_manifest(tree, manifest)
            del original['files']['../outside']
            del original['files']['agents/response-policy.json']
            manifest.write_text(json.dumps(original))
            with self.assertRaises(ValueError): module.source_manifest(tree, manifest)

    def test_previous_ready_snapshot_cannot_satisfy_current_template_acceptance(self):
        """A retained blue/green predecessor may remain Ready but is not the migrated generation's template."""
        module = self.module()
        self.assertTrue(callable(getattr(module, 'current_template', None)), 'Current-generation template selector is missing')
        agent = {'metadata': {'name': 'iot-ab-retrieval-a', 'uid': 'owned-agent', 'generation': 2}}
        current = {'metadata': {'name': 'current-template', 'namespace': 'iot-agents', 'uid': 'current-uid',
            'labels': {'kagent.dev/sandbox-agent': 'iot-ab-retrieval-a'},
            'annotations': {'kagent.dev/desired-generation': '2'},
            'ownerReferences': [{'uid': 'owned-agent', 'name': 'iot-ab-retrieval-a', 'kind': 'SandboxAgent', 'controller': True}]},
            'spec': {'sandboxClass': 'gvisor', 'workerSelector': {'matchLabels': {'kagent.dev/worker-pool': 'iot-retrieval-pool'}}},
            'status': {'phase': 'Ready', 'goldenSnapshot': 'gs://actual-current-snapshot'}}
        old = deepcopy(current)
        old['metadata'].update(name='previous-template', uid='previous-uid')
        old['metadata']['annotations']['kagent.dev/desired-generation'] = '1'
        self.assertEqual(module.current_template([old, current], agent), current)
        with self.assertRaises(ValueError): module.current_template([old], agent)
        for mutate in [lambda t: t['metadata']['ownerReferences'][0].update(uid='different-agent'),
                       lambda t: t['spec'].update(sandboxClass='unconfined'),
                       lambda t: t['spec']['workerSelector']['matchLabels'].update({'kagent.dev/worker-pool': 'other'}),
                       lambda t: t['status'].update(goldenSnapshot='')]:
            changed = deepcopy(current)
            mutate(changed)
            with self.assertRaises(ValueError): module.current_template([changed], agent)
