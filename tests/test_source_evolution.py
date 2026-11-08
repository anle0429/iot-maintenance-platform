"""Keep historical evidence checks strict while the current runtime evolves through reviewed revisions."""
from pathlib import Path
from unittest.mock import patch
from hashlib import sha256
import inspect
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from source_evolution import recorded_source, validate_recorded_seal
import source_evolution
from scripts.git_sources import historical_blob

LEGACY = '888f7a57c1cd815967ba12e2244687116cbffe99'
PREVIOUS = 'f12ea06da2e039d139638c154470f896ed23a7cf'
ACCEPTED = '9471bfe0b2adf3230ea88835df033062fd77a803'
CURRENT = 'ad2da29262e3088c942a9240d7157708c094aca4'


class SourceEvolutionTests(unittest.TestCase):
    """An exact declared replacement is distinct from accepting arbitrary changes to old proof."""

    def test_exact_reviewed_current_source_resolves_to_the_previous_checkpoint(self):
        """Old receipts see authenticated immutable predecessor bytes, never a modified snapshot."""
        name = 'llm_observability/metrics.py'
        old = recorded_source(name)
        self.assertNotEqual(old, (ROOT / name).read_bytes())
        with self.assertRaises(ValueError): recorded_source(name, (ROOT / name).read_bytes() + b'\n')
        self.assertEqual(recorded_source('services/mcp_tools/app.py'), (ROOT / 'services/mcp_tools/app.py').read_bytes())

    def test_changed_transition_inventory_and_escaped_paths_are_rejected(self):
        """Resealing a local manifest cannot redefine the allowed source transition."""
        original = Path.read_bytes

        def changed(path):
            """Alter only the transition inventory in memory for this negative test."""
            raw = original(path)
            return raw + b'\n' if path.name == 'source_evolution.json' else raw

        with patch.object(Path, 'read_bytes', changed), self.assertRaises(ValueError):
            recorded_source('llm_observability/metrics.py')
        with self.assertRaises(ValueError): recorded_source('../outside.py', b'')
        with self.assertRaises(ValueError): validate_recorded_seal({'format': 'other', 'commit': 'a' * 40}, 'a' * 40)

    def selected(self, name, checkpoint, current=None):
        """Require an explicit supported checkpoint API before using the new source view."""
        self.assertIn('checkpoint', inspect.signature(recorded_source).parameters)
        return recorded_source(name, current, checkpoint=checkpoint)

    def test_explicit_checkpoints_authenticate_latest_bytes_before_returning_exact_history(self):
        """One changed runtime file resolves independently to all four exact source checkpoints."""
        name = 'agents/ci_contract.py'
        for checkpoint in (LEGACY, PREVIOUS, ACCEPTED, CURRENT):
            self.assertEqual(self.selected(name, checkpoint), historical_blob(ROOT, checkpoint + ':' + name))
        self.assertEqual(recorded_source(name), historical_blob(ROOT, LEGACY + ':' + name))
        old_current = historical_blob(ROOT, PREVIOUS + ':' + name)
        self.assertNotEqual(old_current, historical_blob(ROOT, CURRENT + ':' + name))
        with self.assertRaises(ValueError): self.selected(name, PREVIOUS, old_current)
        with self.assertRaises(ValueError): self.selected(name, CURRENT, (ROOT/name).read_bytes() + b'\n')
        with self.assertRaises(ValueError): self.selected(name, 'main')
        with self.assertRaises(ValueError): self.selected(name, 'a' * 40)

    def test_legacy_integration_adapter_does_not_impersonate_f12_as_run_bytes(self):
        """The frozen after_blob adapter remains authenticated while f12 selects its actual Git file."""
        name = 'scripts/integration_evidence.py'
        original = historical_blob(ROOT, PREVIOUS + ':' + name)
        adapter = (ROOT/name).read_bytes().replace(b'\r\n', b'\n')
        self.assertNotEqual(original, adapter)
        self.assertEqual(self.selected(name, PREVIOUS), original)
        self.assertEqual(self.selected(name, CURRENT), adapter)
        self.assertEqual(recorded_source(name), historical_blob(ROOT, LEGACY + ':' + name))

    def test_new_source_files_cannot_be_returned_as_nonexistent_historical_inputs(self):
        """A source added in947 is valid for947/ad2 and absent from earlier source checkpoints."""
        name = 'agents/gateway_acceptance.py'
        self.assertEqual(self.selected(name, ACCEPTED), historical_blob(ROOT, ACCEPTED + ':' + name))
        self.assertEqual(self.selected(name, CURRENT), historical_blob(ROOT, CURRENT + ':' + name))
        for checkpoint in (LEGACY, PREVIOUS):
            with self.subTest(checkpoint=checkpoint), self.assertRaises(ValueError): self.selected(name, checkpoint)

    def test_accepted947_source_remains_distinct_from_latest_helm_mount_fix(self):
        """The actual947 policy/chat source and corrected ad2 CI source cannot stand in for each other."""
        self.assertIn(CURRENT, source_evolution.CHECKPOINT_COMMITS, 'Corrected CI checkpoint is missing')
        for name in ('scripts/ci-agents-policy.py', 'tests/test_agent_admission_ci.py'):
            before = historical_blob(ROOT, ACCEPTED + ':' + name)
            after = historical_blob(ROOT, CURRENT + ':' + name)
            self.assertNotEqual(before, after)
            self.assertEqual(self.selected(name, ACCEPTED), before)
            self.assertEqual(self.selected(name, CURRENT), after)
            with self.assertRaises(ValueError): self.selected(name, ACCEPTED, before)

    def test_checkpoint_inventory_and_rehashed_source_or_adapter_mutations_are_rejected(self):
        """A changed inventory, invented historical digest or changed adapter cannot redefine old proof."""
        path = ROOT/'docs/source_checkpoints.json'
        self.assertTrue(path.is_file(), 'Pinned checkpoint inventory is missing')
        original = Path.read_bytes
        raw = original(path).replace(b'\r\n', b'\n')
        for mutation in ('newline', 'source', 'adapter', 'missing-legacy'):
            value = json.loads(raw)
            if mutation == 'source': value['files']['agents/ci_contract.py']['sha256'][PREVIOUS] = '0' * 64
            elif mutation == 'adapter': value['files']['scripts/integration_evidence.py']['legacy_adapter_sha256'] = '0' * 64
            elif mutation == 'missing-legacy': value['files'].pop('llm_observability/metrics.py')
            changed_raw = raw + b'\n' if mutation == 'newline' else (json.dumps(value, indent=2) + '\n').encode()

            def changed(selected_path):
                """Replace only checkpoint inventory bytes in memory for the negative case."""
                return changed_raw if selected_path == path else original(selected_path)

            with self.subTest(mutation=mutation), patch.object(Path, 'read_bytes', changed):
                if mutation == 'newline':
                    with self.assertRaises(ValueError): self.selected('agents/ci_contract.py', PREVIOUS)
                else:
                    self.assertTrue(hasattr(source_evolution, 'CHECKPOINTS_SHA256'))
                    with patch.object(source_evolution, 'CHECKPOINTS_SHA256', sha256(changed_raw).hexdigest()):
                        with self.assertRaises(ValueError): self.selected('agents/ci_contract.py', PREVIOUS)
