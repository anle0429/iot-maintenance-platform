"""Reject partial or contradictory notebook output rather than accepting a passing summary alone."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('notebook_evidence', ROOT / 'scripts/verify-agent-notebook.py')
evidence = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(evidence)


class NotebookEvidenceTests(unittest.TestCase):
    """Use independent stdout fragments; native artifacts are checked separately by the offline verifier."""

    def test_complete_consecutive_json_after_the_actual_question(self):
        """Multiple print calls remain attributable without discarding their prompt or final object."""
        cell = {'outputs':[{'output_type':'stream','name':'stdout','text':'question\n{"task":1}\n{"passed":true}\n'}]}
        self.assertEqual(evidence.output_objects(cell, 'question'), [{'task':1},{'passed':True}])
        with self.assertRaises(AssertionError): evidence.output_objects(cell, 'another question')

    def test_truncation_unrelated_suffix_and_error_output_fail(self):
        """A green object cannot conceal an incomplete response, extra claim or stderr/error event."""
        for value in ['{"passed":true}\n{"task":', '{"passed":true}\nignored claim']:
            cell = {'outputs':[{'output_type':'stream','name':'stdout','text':value}]}
            with self.subTest(value=value), self.assertRaises(ValueError): evidence.output_objects(cell)
        for item in [{'output_type':'error','ename':'RuntimeError'}, {'output_type':'stream','name':'stderr','text':'failed'}]:
            with self.subTest(item=item), self.assertRaises(AssertionError): evidence.output_objects({'outputs':[item]})


if __name__ == '__main__': unittest.main()
