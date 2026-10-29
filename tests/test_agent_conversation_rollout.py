"""Exercise source/resource guards, generation-aware readiness and immutable catalog-version identities without cloud access."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import importlib.util
import json
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    """Import an operator's pure guard functions without invoking its CLI or contacting Kubernetes."""
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


ROLLOUT = module('conversation_rollout', 'configure-agent-conversations.py')
CATALOG = module('agent_catalog', 'publish-iot-agents.py')


class ConversationRolloutTests(unittest.TestCase):
    """Require unchanged source, adequate reserves and current readiness before any prepared policy can be promoted."""

    def test_only_consistent_known_prior_versions_can_be_upgraded(self):
        """A prompt patch cannot overwrite an unrelated version, duplicate name or partially upgraded role set."""
        names = {'iot-retrieval', 'iot-drift', 'iot-coordinator'}
        agents = [{'metadata': {'name': n}, 'spec': {'version': '0.2.0'}} for n in sorted(names)]
        self.assertTrue(ROLLOUT.reviewed_prior_agents(agents, names))
        for mode in ['mixed', 'future', 'duplicate', 'missing']:
            changed = deepcopy(agents)
            if mode == 'mixed': changed[0]['spec']['version'] = '0.1.0'
            elif mode == 'future':
                for agent in changed: agent['spec']['version'] = '0.9.0'
            elif mode == 'duplicate': changed.append(deepcopy(changed[0]))
            else: changed.pop()
            with self.subTest(mode=mode): self.assertFalse(ROLLOUT.reviewed_prior_agents(changed, names))

    def test_source_seal_rejects_modification_and_escape(self):
        """Even a correctly hashed source member must stay inside the sealed tree."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source = root / 'data/llm-source/test/tree'; source.mkdir(parents=True)
            for name in ROLLOUT.REQUIRED_SOURCE:
                path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'original')
            path = source / 'agents/response-policy.json'
            seal = {'files': {name: sha256(b'original').hexdigest() for name in ROLLOUT.REQUIRED_SOURCE}}
            manifest = source.parent / 'manifest.json'; manifest.write_text(json.dumps(seal))
            self.assertEqual(ROLLOUT.approved_source(root, source), seal)
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError): ROLLOUT.approved_source(root, source)
            outside = source.parent / 'outside'; outside.write_bytes(b'known')
            path.write_bytes(b'original'); seal['files']['../outside'] = sha256(b'known').hexdigest()
            manifest.write_text(json.dumps(seal))
            with self.assertRaises(ValueError): ROLLOUT.approved_source(root, source)
            seal['files'].pop('../outside'); seal['files'].pop('scripts/render-agent-responses.py')
            manifest.write_text(json.dumps(seal))
            with self.assertRaises(ValueError): ROLLOUT.approved_source(root, source)

    def test_insufficient_session_ram_or_disk_is_rejected(self):
        """The guard independently rejects each resource shortfall, while accepting exact documented edges."""
        now = datetime(2026, 10, 29, 10, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); session = root / 'data/runtime/ci/session.json'; session.parent.mkdir(parents=True)
            for seconds, ram_gib, disk_gib, accepted in [(4500, 16, 25, True), (4499, 32, 40, False),
                                                       (7200, 15, 40, False), (7200, 32, 24, False)]:
                session.write_text(json.dumps({'auto_stop_utc': (now + timedelta(seconds=seconds)).isoformat()}))
                with self.subTest(seconds=seconds, ram=ram_gib, disk=disk_gib), \
                        patch.object(Path, 'read_text', return_value=f'MemAvailable: {ram_gib * 1024**2} kB\n'), \
                        patch.object(ROLLOUT.shutil, 'disk_usage', return_value=SimpleNamespace(free=disk_gib * 1024**3)):
                    if accepted:
                        report = ROLLOUT.resources(root, now)
                        self.assertFalse(report['new_cloud_resources']); self.assertFalse(report['session_extended'])
                    else:
                        with self.assertRaises(ValueError): ROLLOUT.resources(root, now)

    def test_ready_from_an_old_generation_is_insufficient(self):
        """A status retained during reconciliation cannot certify the newly applied prompt version."""
        objects = json.loads((ROOT / 'docs/evidence/agent-platform/readback/agents.stdout').read_bytes())['items']
        agents = [a for a in objects if a['kind'] == 'SandboxAgent']; names = {a['metadata']['name'] for a in agents}
        self.assertTrue(ROLLOUT.reconciled_agents(agents, names))
        for mode in ['generation', 'condition-generation', 'not-ready', 'missing']:
            changed = deepcopy(agents)
            if mode == 'generation': changed[0]['metadata']['generation'] += 1
            elif mode == 'condition-generation': changed[0]['status']['conditions'][1]['observedGeneration'] = 0
            elif mode == 'not-ready': changed[0]['status']['conditions'][1]['status'] = 'False'
            else: changed.pop()
            with self.subTest(mode=mode): self.assertFalse(ROLLOUT.reconciled_agents(changed, names))

    def test_catalog_uses_the_observed_version(self):
        """A new response policy gets its own tag instead of being published over the original version."""
        self.assertEqual(CATALOG.catalog_version({'spec': {'version': '0.2.0'}}), '0.2.0')
        for version in [None, '../0.1.0', 'latest', '0.1', 0.2]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                CATALOG.catalog_version({'spec': {'version': version}})

    def test_catalog_identity_cannot_be_replaced(self):
        """Readback/preflight annotations must bind the exact native resource and full configuration hash."""
        entry = {'metadata': {'tag': '0.2.0', 'annotations': {'iot.coursework/resource-uid': 'native-uid',
                  'iot.coursework/spec-sha256': 'known-spec'}}, 'spec': {'source': {'image': 'immutable-image'}}}
        CATALOG.assert_catalog_identity(json.dumps(entry).encode(), 'native-uid', 'known-spec', 'immutable-image', '0.2.0')
        for uid, digest, image, version in [('other-uid', 'known-spec', 'immutable-image', '0.2.0'),
                ('native-uid', 'another-spec', 'immutable-image', '0.2.0'),
                ('native-uid', 'known-spec', 'another-image', '0.2.0'),
                ('native-uid', 'known-spec', 'immutable-image', '0.3.0')]:
            with self.subTest(uid=uid, digest=digest, image=image, version=version), self.assertRaises(ValueError):
                CATALOG.assert_catalog_identity(json.dumps(entry).encode(), uid, digest, image, version)

    def test_context_and_private_endpoint_are_required(self):
        """Do not fall back to a small context, another model endpoint or disabled TLS when raising the answer budget."""
        current = json.loads((ROOT / 'docs/evidence/agent-platform/model-budget/native/values.json').read_bytes())
        properties = {'default_generation_settings': {'n_ctx': 8192}}
        ROLLOUT.validate_runtime(current, properties)
        for mode in ['small-context', 'external-endpoint', 'disabled-tls', 'different-model']:
            candidate = deepcopy(current); props = deepcopy(properties)
            if mode == 'small-context': props['default_generation_settings']['n_ctx'] = 4096
            elif mode == 'external-endpoint': candidate['providers']['openAI']['config']['baseUrl'] = 'https://unapproved.example/v1'
            elif mode == 'disabled-tls': candidate['controller']['substrate']['ateApiInsecure'] = True
            else: candidate['providers']['openAI']['model'] = 'another-model'
            with self.subTest(mode=mode), self.assertRaises(ValueError): ROLLOUT.validate_runtime(candidate, props)
