"""Reject large source runs when correctness, source identity, disk, RAM or session headroom is missing."""

from copy import deepcopy
import unittest

from pipelines.scale_budget import scale_plan


class ScaleBudgetTests(unittest.TestCase):
    """Use simple arithmetic fixtures; no forecast is accepted as actual 100GB evidence."""

    def setUp(self):
        """Represent a verified 5GB intermediate and conservative resource availability."""
        self.measured = {'passed': True, 'actual_offline_payload_bytes': 5_000_000_000, 'unique_rows': 10_000_000,
                         'source_manifest_sha256': 'same-source', 'phase_reports':
                         {name: {'passed': True, 'seconds': 100} for name in ('generate', 'validate')}}
        self.ingest = {'passed': True, 'source_manifest_sha256': 'same-source', 'source_offline_bytes': 5_000_000_000,
                       'silver_unique_rows': 10_000_000}
        self.available = {'free_bytes': {'/srv/iot-data': 500_000_000_000, '/srv/iot-fast': 130_000_000_000},
                          'memory_available_bytes': 50 * 1024**3}

    def test_all_gates_and_exact_budget(self):
        """Budget source, downstream and reserves separately; never mark future Spark as executed."""
        plan = scale_plan(self.measured, self.ingest, self.available, 204_000_000, 10_000)
        self.assertTrue(plan['generation_eligible'])
        self.assertEqual(sum(plan['hdd_budget_bytes'].values()), 400_000_000_000)
        self.assertEqual(plan['source_reserve_bytes'], 285_000_000_000)
        self.assertEqual(plan['estimated_offline_payload_bytes'], 102_000_000_000)
        self.assertFalse(plan['large_spark_execution_authorized'])

    def test_each_resource_gate_blocks_the_run(self):
        """One failing resource must be enough even if the other three are abundant."""
        for gate in ('hdd', 'ssd', 'ram', 'session_time'):
            actual = deepcopy(self.available)
            if gate in ('hdd', 'ssd'):
                actual['free_bytes']['/srv/iot-data' if gate == 'hdd' else '/srv/iot-fast'] = 1
            elif gate == 'ram':
                actual['memory_available_bytes'] = 1
            plan = scale_plan(self.measured, self.ingest, actual, 204_000_000, 1 if gate == 'session_time' else 10_000)
            self.assertFalse(plan['generation_eligible'], gate)
            self.assertFalse(plan['gates'][gate])

    def test_failed_or_mismatched_intermediate_is_rejected(self):
        """A passing unrelated Spark run cannot authorize another source's big run."""
        for field, value in [('passed', False), ('source_manifest_sha256', 'another'), ('silver_unique_rows', 1)]:
            ingest = {**self.ingest, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                scale_plan(self.measured, ingest, self.available, 204_000_000, 10_000)
        with self.assertRaises(ValueError):
            scale_plan({**self.measured, 'actual_offline_payload_bytes': 10}, self.ingest, self.available, 204_000_000, 10_000)


if __name__ == '__main__':
    unittest.main()
