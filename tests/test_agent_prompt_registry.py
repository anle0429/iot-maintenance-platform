"""Verify that only exact registered prompt versions can authorize a release."""

from types import SimpleNamespace
import unittest

from agents.prompt_registry import verify_prompts


class PromptRegistryTest(unittest.TestCase):
    """Check the registry boundary for the complete shared Helm release."""

    prompts = dict(retrieval='retrieve', drift='detect', coordinator='coordinate')
    versions = dict(retrieval=4, drift=2, coordinator=8)

    def load(self, uri):
        """Return the fixture for the exact URI requested by the release."""
        role, version = uri.removeprefix('prompts:/iot-agent-').split('/')
        return SimpleNamespace(name='iot-agent-' + role, version=int(version), template=self.prompts[role])

    def test_pins_all_roles(self):
        """Record the exact version and digest for all three roles."""
        result = verify_prompts(self.prompts, self.versions, self.load)
        self.assertEqual({role: row['version'] for role, row in result.items()}, self.versions)
        self.assertTrue(all(len(row['sha256']) == 64 for row in result.values()))

    def test_rejects_registry_drift(self):
        """Stop before deployment when the registry content differs."""
        changed = dict(self.prompts, drift='different')
        with self.assertRaisesRegex(ValueError, 'drift'):
            verify_prompts(changed, self.versions, self.load)

    def test_rejects_incomplete_release(self):
        """Require every agent in the shared release even for one role's job."""
        with self.assertRaises(ValueError):
            verify_prompts(self.prompts, {'retrieval': 4}, self.load)

    def test_rejects_returned_version_mismatch(self):
        """Refuse a registry response that substitutes another version."""
        def load_other_version(uri):
            """Simulate a wrong version with otherwise identical text."""
            prompt = self.load(uri)
            prompt.version += 1
            return prompt

        with self.assertRaisesRegex(ValueError, 'retrieval'):
            verify_prompts(self.prompts, self.versions, load_other_version)
