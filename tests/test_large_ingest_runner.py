"""Check that large-run timing admission cannot be built from unrelated, partial or changed pilot evidence."""

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('large_ingest_runner', Path(__file__).resolve().parents[1] / 'scripts/run-large-ingest.py')
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class LargeIngestForecastTests(unittest.TestCase):
    """Use hand-computed bytes/timing and bound files so the gate is tested independently from live Spark."""

    def setUp(self):
        """Create finite pilot artifacts, including exact report/verification SHA bindings."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.folder = self.root / 'outputs/pilot'
        self.folder.mkdir(parents=True)
        self.binding = {'source_offline_bytes': 100_000_000_000, 'source_manifest_sha256': 'large'}
        self.hashes = {'job.py': 'unchanged'}
        self.report = {'passed': True, 'source_offline_bytes': 2_000_000_000, 'source_manifest_sha256': 'small',
                       'query_comparison': {'repetitions': 3}, 'execution': {'master': 'local[6]', 'output_partitions': 128,
                                                                          'aqe_coalesce_partitions': 'false'}}
        self.metrics = {'application_ended': True, 'failed_attempts': 0, 'task_attempts': 50,
                        'input_bytes_read': 7_000_000_000, 'output_bytes_written': 1_000_000_000,
                        'shuffle_bytes_read': 1_000_000_000, 'shuffle_bytes_written': 1_000_000_000, 'disk_bytes_spilled': 0,
                        'max_task_peak_execution_memory': 200_000_000}
        self.verification = {'passed': True, 'execution_sha256': self.hashes, 'image_id': 'image', 'partitions': 128,
                             'spark_command_seconds': 100, 'source_manifest_sha256': 'small', 'source_offline_bytes': 2_000_000_000}
        self.samples = {'finished': True, 'peak_scratch_file_bytes': 1_000_000_000, 'peak_output_file_bytes': 100_000_000}
        self.probe = self.root / 'probe.json'
        self.probe.write_text(json.dumps({'probe': {'passed': True, 'source_manifest_sha256': 'large',
                                                   'bytes_per_second': 128_000_000}}))
        self.bind_files()

    def bind_files(self):
        """Seal fixture reports as an actual runner would; mutations without resealing remain detectable."""
        for name, value, key in [('ingest-report.json', self.report, 'ingest_report_sha256'),
                                  ('spark-metrics.json', self.metrics, 'metrics_sha256'),
                                  ('resource-samples.json', self.samples, 'resource_samples_sha256')]:
            raw = json.dumps(value).encode()
            (self.folder / name).write_bytes(raw)
            self.verification[key] = sha256(raw).hexdigest()
        raw = json.dumps(self.verification).encode()
        (self.folder / 'verification.json').write_bytes(raw)
        (self.folder / '_RUN_SUCCESS.json').write_text(json.dumps({'verification_sha256': sha256(raw).hexdigest()}))

    def forecast(self, hashes=None):
        """Point only this filesystem-bound fixture at the production forecast function."""
        with patch.object(RUNNER, 'ROOT', self.root):
            return RUNNER.forecast(self.binding, 'pilot', 'image', hashes or self.hashes, self.probe)

    def test_hand_computed_total_keeps_two_full_checksum_passes(self):
        """Fifty times 10GB task IO plus two 100GB scans is 700GB, at a capped 64MB/s and margin1.3."""
        result = self.forecast()
        self.assertEqual(result['estimated_io_bytes'], 700_000_000_000)
        self.assertEqual(result['checksum_passes'], 2)
        self.assertEqual(result['estimated_seconds'], 14218.75)
        self.assertEqual(result['forecast_disk_bytes_per_second'], 64_000_000)
        self.assertEqual(result['projected_peak_scratch_bytes'], 195_000_000_000)

    def test_changed_runtime_cannot_reuse_a_pilot(self):
        """A successful pilot of another code snapshot cannot admit this large job."""
        with self.assertRaisesRegex(ValueError, 'runtime/image/partition'):
            self.forecast({'job.py': 'changed'})

    def test_changed_report_bytes_are_rejected(self):
        """A valid marker cannot hide a report changed after its verification was sealed."""
        (self.folder / 'ingest-report.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'report binding'):
            self.forecast()

    def test_projected_scratch_or_output_must_fit_live_caps(self):
        """Enough total free disk must not hide a projected job that will hit its own scratch/output guard."""
        for key, value in [('peak_scratch_file_bytes', 4_000_000_000), ('peak_output_file_bytes', 2_000_000_000)]:
            before = self.samples.copy()
            self.samples[key] = value
            self.bind_files()
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'projected scratch/output'):
                self.forecast()
            self.samples = before

    def test_resource_samples_cannot_change_after_verification(self):
        """A smaller edited scratch peak cannot falsely admit the job under a still-valid verification marker."""
        (self.folder / 'resource-samples.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'report binding'):
            self.forecast()

    def test_task_memory_and_partition_budget_are_checked_before_launch(self):
        """Plentiful host RAM cannot substitute for enough partitions inside the fixed Spark heap."""
        self.metrics['max_task_peak_execution_memory'] = 2_000_000_000
        self.bind_files()
        with self.assertRaisesRegex(ValueError, 'concurrent task memory'):
            self.forecast()

    def test_incomplete_or_failed_metrics_cannot_admit_large_input(self):
        """Finished Spark application and nonzero successful task evidence are required independently."""
        for key, value in [('application_ended', False), ('failed_attempts', 1), ('task_attempts', 0)]:
            before = self.metrics.copy()
            self.metrics[key] = value
            self.bind_files()
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'full successful'):
                self.forecast()
            self.metrics = before

    def test_wrong_pilot_input_or_query_workload_is_rejected(self):
        """A smaller query or a different input cannot masquerade as the reviewed full workload."""
        self.report['source_manifest_sha256'] = 'unrelated'
        self.bind_files()
        with self.assertRaisesRegex(ValueError, 'full successful'):
            self.forecast()

    def test_intermediate_cap_is_not_a_large_run_estimate(self):
        """The first run is explicitly a bounded pilot and cannot bootstrap a large source without evidence."""
        result = RUNNER.forecast({'source_offline_bytes': 5_000_000_000}, None, 'image', {}, self.probe)
        self.assertEqual(result['kind'], 'bounded_intermediate_first')
        self.assertEqual(result['estimated_seconds'], 5400)
        with self.assertRaisesRegex(ValueError, 'requires a successful'):
            RUNNER.forecast(self.binding, None, 'image', {}, self.probe)


if __name__ == '__main__':
    unittest.main()
