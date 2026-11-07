"""Keep historical evidence checks strict while the current runtime evolves through reviewed revisions."""
from pathlib import Path
from unittest.mock import patch
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from source_evolution import recorded_source, validate_recorded_seal


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
