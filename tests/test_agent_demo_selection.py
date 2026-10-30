"""Keep a learner demo scoped to the original production agents when experimental agents also exist."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('agent_demo', Path(__file__).resolve().parents[1]/'scripts/run-agent-demo.py')
DEMO = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(DEMO)


class DemoSelectionTests(unittest.TestCase):
    """Select exact identities without renaming, modifying or silently accepting an incomplete production set."""

    def test_extra_experiments_are_preserved(self):
        """Pass the same production objects to readiness checks while leaving experimental objects untouched."""
        items = [{'metadata': {'name': name}, 'spec': {'marker': name}} for name in sorted(DEMO.PRODUCTION)]
        extra = {'metadata': {'name': 'iot-ab-retrieval-a'}, 'spec': {'temperature': '0'}}
        selected = DEMO.production_agents([extra, *items])
        self.assertEqual(selected, items)
        self.assertTrue(all(a is b for a, b in zip(selected, items)))
        self.assertEqual(extra, {'metadata': {'name': 'iot-ab-retrieval-a'}, 'spec': {'temperature': '0'}})

    def test_missing_or_duplicate_production_agent_fails(self):
        """Refuse partial or ambiguous live inventories instead of substituting an experimental agent."""
        items = [{'metadata': {'name': name}} for name in sorted(DEMO.PRODUCTION)]
        for value in [items[:2], [*items, items[0]], [{'metadata': {'name': 'iot-ab-retrieval-b'}}]]:
            with self.subTest(value=value), self.assertRaises(ValueError): DEMO.production_agents(value)


if __name__ == '__main__': unittest.main()
