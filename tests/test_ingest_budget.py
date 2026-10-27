"""Exercise resource boundaries, current-source binding and honest time rejection for the large ingest forecast."""

from copy import deepcopy
import math
import unittest

from pipelines.ingest_budget import large_ingest_plan


class IngestBudgetTests(unittest.TestCase):
    """Small measured fixtures isolate admission decisions without pretending to execute a large Spark job."""

    def setUp(self):
        """Use a 20-fold source ratio, with known IO counters and explicit per-volume reserves."""
        self.args = [dict(passed=True, source_manifest_sha256='large', actual_offline_payload_bytes=100_000_000_000),
                     dict(passed=True, source_manifest_sha256='small', actual_offline_payload_bytes=5_000_000_000, unique_rows=10),
                     dict(passed=True, source_manifest_sha256='small', source_offline_bytes=5_000_000_000,
                          silver_unique_rows=10, job_wall_seconds=100),
                     dict(failed_attempts=0, task_attempts=10, input_bytes_read=30_000_000_000,
                          output_bytes_written=1_000_000_000, shuffle_bytes_read=5_000_000_000,
                          shuffle_bytes_written=5_000_000_000, disk_bytes_spilled=0),
                     dict(passed=True, source_manifest_sha256='large', bytes_per_second=100_000_000, actual_read_bytes=536870912),
                     dict(free_bytes={'/srv/iot-data': 305_000_000_000, '/srv/iot-fast': 35_000_000_000},
                          memory_available_bytes=36 * 1024**3, cpu_count=7), 100000]

    def test_exact_resource_boundaries_and_no_execution_claim(self):
        """Already stored source consumes no new reserve; three checksum scans remain in the time model."""
        before = deepcopy(self.args)
        result = large_ingest_plan(*self.args)
        self.assertTrue(result['resource_time_gates_pass'])
        self.assertFalse(result['execution_ready'])
        self.assertEqual(result['estimated_total_io_bytes'], 1_120_000_000_000)
        self.assertEqual(result['checksum_passes_in_current_launcher'], 3)
        self.assertEqual(self.args, before)

    def test_each_insufficient_volume_ram_and_cpu_blocks(self):
        """One byte or one CPU below each exact resource gate cannot be masked by other plentiful resources."""
        for key in ('hdd', 'ssd', 'ram', 'cpu'):
            with self.subTest(key=key):
                args = deepcopy(self.args)
                r = args[5]
                if key in ('hdd', 'ssd'):
                    r['free_bytes']['/srv/iot-data' if key == 'hdd' else '/srv/iot-fast'] -= 1
                else:
                    r['memory_available_bytes' if key == 'ram' else 'cpu_count'] -= 1
                self.assertFalse(large_ingest_plan(*args)['gates'][key])

    def test_session_time_includes_io_margin_and_finish(self):
        """Spark wall-time alone fits, while repeated reads force rejection until the full forecast fits."""
        exact = large_ingest_plan(*self.args)['estimated_job_seconds'] + 1200
        self.args[-1] = exact
        self.assertTrue(large_ingest_plan(*self.args)['gates']['session_time'])
        self.args[-1] -= .001
        self.assertFalse(large_ingest_plan(*self.args)['gates']['session_time'])

    def test_mismatched_failed_or_nonfinite_evidence_rejected(self):
        """A plausible capacity cannot substitute for correctly bound, successful finite measurements."""
        changes = [(1, 'source_manifest_sha256', 'wrong'), (4, 'source_manifest_sha256', 'wrong'),
                   (0, 'passed', False), (3, 'failed_attempts', 1), (4, 'bytes_per_second', math.nan),
                   (4, 'bytes_per_second', True), (3, 'shuffle_bytes_read', -1)]
        for position, key, value in changes:
            with self.subTest(key=key, value=value):
                args = deepcopy(self.args)
                args[position][key] = value
                with self.assertRaises(ValueError):
                    large_ingest_plan(*args)


if __name__ == '__main__':
    unittest.main()
