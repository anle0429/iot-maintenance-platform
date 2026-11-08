"""Keep the fifth A/B gateway checkpoint distinct from all recorded production source views."""
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
import json
import unittest
from scripts import source_evolution
from scripts.git_sources import historical_blob

ROOT = Path(__file__).resolve().parents[1]
OLD = ('888f7a57c1cd815967ba12e2244687116cbffe99', 'f12ea06da2e039d139638c154470f896ed23a7cf', '9471bfe0b2adf3230ea88835df033062fd77a803', 'ad2da29262e3088c942a9240d7157708c094aca4')
CURRENT = '1e883f4c68ea27f973f9191f4e187e0438a9437c'


class ABGatewaySourceEvolutionTests(unittest.TestCase):
    """A narrow reviewed candidate change cannot rewrite historical runtime evidence."""

    def test_exact_fifth_checkpoint_retains_all_four_predecessors(self):
        """Legacy receipts continue selecting their exact immutable source identities."""
        self.assertEqual(source_evolution.CHECKPOINT_COMMITS, OLD + (CURRENT,))
        transition, inventory = source_evolution.reviewed_inventories()
        self.assertEqual(len(inventory['files']), 48)
        self.assertEqual(inventory['corrected_ci_commit'], OLD[-1])
        self.assertEqual(inventory['current_commit'], CURRENT)
        self.assertEqual(transition['after_commit'], OLD[1])
        name = 'infra/agentgateway/candidate.py'
        for commit in OLD + (CURRENT,):
            self.assertEqual(source_evolution.recorded_source(name, checkpoint=commit),
                             historical_blob(ROOT, commit + ':' + name))
        self.assertEqual(source_evolution.recorded_source(name), historical_blob(ROOT, OLD[0] + ':' + name))

    def test_previous_source_cannot_impersonate_latest_working_bytes(self):
        """Historical source is returned only after authenticating the new runtime bytes."""
        name = 'infra/agentgateway/candidate.py'
        latest = historical_blob(ROOT, CURRENT + ':' + name)
        previous = historical_blob(ROOT, OLD[-1] + ':' + name)
        self.assertNotEqual(latest, previous)
        for old in OLD:
            with self.subTest(commit=old), self.assertRaises(ValueError):
                source_evolution.recorded_source(name, previous, checkpoint=old)
        with self.assertRaises(ValueError):
            source_evolution.recorded_source(name, latest + b'\n', checkpoint=CURRENT)

    def test_rehashed_latest_digest_cannot_redefine_an_immutable_checkpoint(self):
        """Even a coordinated inventory hash edit cannot substitute an invented Git-source digest."""
        path = ROOT / 'docs/source_checkpoints.json'
        original = Path.read_bytes
        value = json.loads(original(path))
        value['files']['infra/agentgateway/candidate.py']['sha256'][CURRENT] = '0' * 64
        altered = (json.dumps(value, indent=2) + '\n').encode()

        def changed(selected):
            """Replace only the proposed checkpoint inventory in memory."""
            return altered if selected == path else original(selected)

        with patch.object(Path, 'read_bytes', changed), patch.object(source_evolution, 'CHECKPOINTS_SHA256', sha256(altered).hexdigest()):
            with self.assertRaises(ValueError):
                source_evolution.recorded_source('infra/agentgateway/candidate.py', checkpoint=OLD[-1])
