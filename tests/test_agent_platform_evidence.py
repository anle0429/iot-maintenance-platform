"""Reject contradictory native agent receipts independently of their inventory-byte checks."""
from pathlib import Path
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('agent_platform_evidence', ROOT / 'scripts/verify-agent-platform.py')
CHECKER = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(CHECKER)


class AgentPlatformEvidenceTests(unittest.TestCase):
    """Keep catalog/config acceptance separate from failed generation and full coursework completion."""

    def test_native_foundation_keeps_open_boundaries(self):
        """Ready native agents and catalog publication cannot silently imply successful complete conversations or scaling."""
        result = CHECKER.verify()
        self.assertTrue(result['agent_platform_foundation_verified'])
        self.assertEqual(result['native_tool_results'], 6)
        for key in ['complete_agent_chat_verified', 'sandbox_boundary_probe_verified', 'autoscaling_verified', 'coursework_complete']:
            self.assertFalse(result[key])

    def test_catalog_cannot_reference_a_different_resource(self):
        """A valid-looking catalog entry with another deployment UID fails the independent native-object comparison."""
        self.reject_change('catalog-and-wire-failure/native/catalog/iot-retrieval-readback.json', 'catalog')

    def test_failed_generation_cannot_be_relabelled_complete(self):
        """Successful downstream tool history does not erase the recorded model-generation failure."""
        self.reject_change('generation-timeout/native/retrieval-response.json', 'task')

    def reject_change(self, name, kind):
        """Alter one copied business receipt and bypass only its byte inventory to exercise the semantic oracle."""
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / 'evidence'; shutil.copytree(ROOT / 'docs/evidence/agent-platform', copied)
            path = copied / name; value = json.loads(path.read_bytes())
            if kind == 'catalog':
                value['metadata']['annotations']['iot.coursework/resource-uid'] = 'another-deployment'
            else:
                value['result']['status']['state'] = 'completed'
            path.write_text(json.dumps(value), encoding='utf-8')
            count = len(json.loads((copied / 'manifest.json').read_bytes())['files'])
            with patch.object(CHECKER, 'capsule', return_value=(copied, count)):
                with self.assertRaises(AssertionError):
                    CHECKER.verify()
