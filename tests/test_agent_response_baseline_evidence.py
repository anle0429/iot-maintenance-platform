"""Keep complete drift facts separate from a failed overall native conversation and reject contradictory decoded receipts."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import importlib.util
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('agent_response_baseline', ROOT / 'scripts/verify-agent-response-baseline.py')
CHECKER = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(CHECKER)


class AgentResponseBaselineTests(unittest.TestCase):
    """Exercise the semantic oracle independently of inventory integrity without editing any original evidence."""

    def test_native_drift_does_not_imply_full_conversation(self):
        """Three drift tool returns cover32unique machines; retrieval and coordinator acceptance remain false."""
        result = CHECKER.verify()
        self.assertTrue(result['drift_answer_verified'])
        self.assertEqual(result['successful_drift_tool_events'], 3)
        self.assertEqual(result['drift_unique_machines'], 32)
        for key in ['retrieval_answer_verified', 'coordinator_answer_verified', 'complete_agent_chat_verified',
                    'response_policy_0_2_deployed', 'coursework_complete']:
            self.assertFalse(result[key])

    def test_final_drift_reference_must_match_its_tool_result(self):
        """A decoded final-answer reference changed after byte verification still fails semantic comparison."""
        original = CHECKER.read

        def altered(base, name):
            """Alter only the decoded final text to isolate the source-of-truth comparison from byte hashing."""
            value = original(base, name)
            if name == 'chat/native/drift-response.json':
                value = deepcopy(value)
                value['result']['artifacts'][0]['parts'][0]['text'] = value['result']['artifacts'][0]['parts'][0]['text'].replace(
                    'ac366302a409d2a6a0f7f0442f8aab42c882f81310d072908b9826159b758e93', 'f' * 64)
            return value

        with patch.object(CHECKER, 'read', side_effect=altered), self.assertRaises(AssertionError): CHECKER.verify()

    def test_completion_state_cannot_be_relabelled(self):
        """Task and results-list states must agree with each other; copied fluent text alone proves nothing."""
        original = CHECKER.read

        def altered(base, name):
            """Return a contradictory decoded transport/task receipt after the original inventory check."""
            value = original(base, name)
            if name == 'chat/native/drift-response.json':
                value = deepcopy(value); value['result']['status']['state'] = 'failed'
            return value

        with patch.object(CHECKER, 'read', side_effect=altered), self.assertRaises(AssertionError): CHECKER.verify()
