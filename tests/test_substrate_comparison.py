"""Validate matched latency comparisons without discarding warmups or failed requests."""
import copy
import unittest
from infra.substrate_comparison import summarize


class SubstrateComparisonTests(unittest.TestCase):
    """Require alternating, complete, accepted pairs and preserve negative results."""

    def test_slower_substrate_is_reported_without_a_speedup_claim(self):
        """Warmups are retained but excluded from the four-pair median."""
        rows = [{'pair': pair, 'arm': arm, 'warmup': pair == 0, 'accepted': True,
                 'http_status': 200, 'elapsed_seconds': 6.0 if arm == 'substrate' else 4.0,
                 'question_sha256': 'a' * 64, 'task': str(pair) + arm}
                for pair in range(5)
                for arm in (['substrate', 'plain'] if pair % 2 == 0 else ['plain', 'substrate'])]
        result = summarize(rows)
        self.assertEqual(result['medians_seconds'], {'substrate': 6.0, 'plain': 4.0})
        self.assertEqual(result['substrate_percent_change'], 50.0)
        self.assertEqual(result['accepted_requests'], 10)
        for case in ['missing', 'failed', 'question', 'order', 'zero', 'duplicate-task']:
            changed = copy.deepcopy(rows)
            if case == 'missing': changed.pop()
            elif case == 'failed': changed[1]['accepted'] = False
            elif case == 'question': changed[1]['question_sha256'] = 'b' * 64
            elif case == 'order': changed[2:4] = reversed(changed[2:4])
            elif case == 'zero': changed[1]['elapsed_seconds'] = 0
            else: changed[1]['task'] = changed[0]['task']
            with self.subTest(case=case), self.assertRaises(ValueError): summarize(changed)
