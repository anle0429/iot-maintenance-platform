"""Reject source-consistent but semantically contradictory MCP receipts without invoking live services."""
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
SPEC = importlib.util.spec_from_file_location('verify_mcp_tools', ROOT / 'scripts/verify-mcp-tools.py')
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class McpEvidenceTests(unittest.TestCase):
    """Distinguish recorded tool success, raw quality failure and full agent completion."""
    def test_original_capsule_accepts_only_its_recorded_scope(self):
        """Native MCP deployment/load may pass while mutation remains explicitly unsuccessful."""
        result = CHECKER.verify()
        self.assertTrue(result['passed'])
        self.assertEqual(result['native_mcp_calls'], 24)
        self.assertEqual(result['locust_requests'], 2658)
        self.assertFalse(result['initial_mutation_gate_passed'])

    def test_changed_prediction_trace_is_rejected(self):
        """Even after bypassing byte-integrity checks, a different machine in the prediction cannot pass the oracle."""
        self.reject_change('success/native/retrieval-probe.stdout', 'prediction')

    def test_claimed_successful_mutation_gate_is_rejected(self):
        """A relabeled passed flag cannot turn 531/945 detected mutants into an accepted mutation gate."""
        self.reject_change('initial-mutation/native/proof/result/verification.json', 'mutation')

    def reject_change(self, name, kind):
        """Change one copied semantic receipt while retaining the independent source and count checks."""
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / 'capsule'
            shutil.copytree(ROOT / 'docs/evidence/mcp-tools', copied)
            path = copied / name; value = json.loads(path.read_bytes())
            if kind == 'mutation':
                value['passed'] = True
            else:
                response = value['calls'][2]['response']['content'][0]
                data = json.loads(response['text']); data['feature_trace']['machine_id'] = 'P-002'
                response['text'] = json.dumps(data)
            path.write_text(json.dumps(value), encoding='utf-8')
            with patch.object(CHECKER, 'capsule', return_value=(copied, 719)):
                with self.assertRaises(AssertionError):
                    CHECKER.verify()
