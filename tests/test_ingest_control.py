"""Check lightweight admission boundaries while proving full Spark-side integrity remains mandatory."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from generator.chunked import ChunkConfig, generate_chunked
from pipelines.chunk_source import ChunkSource, validation_receipt
from pipelines.ingest_control import (GIB, admission, deadline_timestamp, guard_reasons,
                                      partition_count, receipt_metadata, select_partitions, suggested_partitions)


class IngestControlTests(unittest.TestCase):
    """Known source corruption and exact resource thresholds exercise the admission/execution distinction."""

    def source(self, directory):
        """Build a small continuous offline source and its real semantic receipt."""
        root, receipt = Path(directory) / 'source', Path(directory) / 'receipt.json'
        generate_chunked(ChunkConfig(machine_count=2, duration_hours=1), root, chunk_rows=20, include_stream=False)
        validation_receipt(root, receipt)
        return root, receipt

    def setUp(self):
        """Place each resource exactly on the accepted admission and live-reserve boundary."""
        self.current = {'free_bytes': {'/srv/iot-data': 355_000_000_000, '/srv/iot-fast': 35_000_000_000},
                        'memory_available_bytes': 36 * GIB, 'cpu_count': 7,
                        'scratch_file_bytes': 0, 'output_file_bytes': 0, 'log_file_bytes': 0}

    def test_metadata_is_bound_but_does_not_claim_a_new_payload_scan(self):
        """Part counts and byte/receipt identity agree with a separately verified full adapter."""
        with tempfile.TemporaryDirectory() as directory:
            root, receipt = self.source(directory)
            bound, full = receipt_metadata(root, receipt), ChunkSource(root, receipt)
            self.assertTrue(bound['metadata_bound'])
            self.assertFalse(bound['payload_checksums_rechecked'])
            self.assertEqual(bound['source_manifest_sha256'], full.manifest_sha256)
            self.assertEqual(bound['source_offline_bytes'], full.offline_bytes)
            self.assertEqual(bound['unique_rows'], 120)

    def test_equal_size_payload_corruption_is_still_caught_by_full_adapter(self):
        """Stat-only host admission cannot certify payload bytes, so the mandatory Spark adapter must reject them."""
        with tempfile.TemporaryDirectory() as directory:
            root, receipt = self.source(directory)
            path = next((root / 'telemetry/schema_v1').glob('part-*.jsonl'))
            raw = path.read_bytes()
            changed = raw.replace(b'"operating"', b'"operation"', 1)
            self.assertNotEqual(raw, changed)
            self.assertEqual(len(raw), len(changed))
            path.write_bytes(changed)
            self.assertFalse(receipt_metadata(root, receipt)['payload_checksums_rechecked'])
            with self.assertRaisesRegex(ValueError, 'part integrity'):
                ChunkSource(root, receipt)

    def test_metadata_size_changes_and_unlisted_files_are_rejected(self):
        """A missing/extra byte or extra source file must fail before an expensive Spark launch."""
        for variant in ('size', 'unlisted'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                root, receipt = self.source(directory)
                if variant == 'size':
                    path = next((root / 'telemetry/schema_v1').glob('part-*.jsonl'))
                    path.write_bytes(path.read_bytes() + b'\n')
                else:
                    (root / 'extra.jsonl').write_bytes(b'{}\n')
                with self.assertRaises(ValueError):
                    receipt_metadata(root, receipt)

    def test_other_source_receipt_is_rejected(self):
        """A passed flag and matching shape cannot substitute for the exact sealed manifest."""
        with tempfile.TemporaryDirectory() as directory:
            root, receipt = self.source(directory)
            raw = receipt.read_text().replace('"passed": true', '"passed": false', 1)
            receipt.write_text(raw)
            with self.assertRaisesRegex(ValueError, 'invalid semantic receipt'):
                receipt_metadata(root, receipt)

    def test_partition_envelope_and_large_source_recommendation(self):
        """A 102GB source gets 2048 partitions; values just outside the reviewed envelope cannot sneak through."""
        self.assertEqual(suggested_partitions(5_587_984_353), 128)
        self.assertEqual(suggested_partitions(102_447_847_856), 2048)
        self.assertEqual(partition_count(128), 128)
        self.assertEqual(partition_count(4096), 4096)
        for value in (0, -1, 4097, True, 128.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                partition_count(value)
        with self.assertRaises(ValueError):
            suggested_partitions(115_000_000_001)

    def test_admission_requires_every_gate_and_finish_margin(self):
        """Exact limits pass; one lost byte or one second below the finish reserve independently blocks launch."""
        self.assertTrue(admission(self.current, 4800, 3600)['admitted'])
        self.assertFalse(admission(self.current, 4799, 3600)['admitted'])
        for key in ('hdd', 'ssd', 'ram', 'cpu'):
            current = deepcopy(self.current)
            if key in ('hdd', 'ssd'):
                current['free_bytes']['/srv/iot-data' if key == 'hdd' else '/srv/iot-fast'] -= 1
            else:
                current['memory_available_bytes' if key == 'ram' else 'cpu_count'] -= 1
            self.assertFalse(admission(current, 4800, 3600)['gates'][key])

    def test_omitted_and_invalid_partition_arguments_remain_distinct(self):
        """Zero must be rejected rather than interpreted as default, and a large source cannot select too few partitions."""
        self.assertEqual(select_partitions(None, 102_447_847_856), 2048)
        self.assertEqual(select_partitions(128, 5_587_984_353), 128)
        with self.assertRaisesRegex(ValueError, '1..4096'):
            select_partitions(0, 5_587_984_353)
        with self.assertRaisesRegex(ValueError, 'recommendation'):
            select_partitions(128, 102_447_847_856)

    def test_live_guards_protect_reserves_and_partial_work(self):
        """A running job is stopped on output, scratch, RAM, disk, deadline or its wall-time cap."""
        current = deepcopy(self.current)
        self.assertEqual(guard_reasons(current, 601, 3599, 3600, pilot=True), [])
        cases = [('scratch_file_bytes', 20_000_000_001), ('output_file_bytes', 20_000_000_001),
                 ('log_file_bytes', 5_000_000_001), ('memory_available_bytes', 4 * GIB - 1)]
        for key, value in cases:
            current = deepcopy(self.current)
            current[key] = value
            self.assertTrue(guard_reasons(current, 601, 3599, 3600, pilot=True))
        self.assertIn('session_finish_window', guard_reasons(self.current, 600, 1, 3600, pilot=False))
        self.assertIn('run_timeout', guard_reasons(self.current, 601, 3600, 3600, pilot=False))
        current = deepcopy(self.current)
        current['free_bytes']['/srv/iot-data'] = 30_000_000_000 - 1
        self.assertIn('hdd_free_reserve', guard_reasons(current, 601, 1, 3600, pilot=False))

    def test_deadline_timezone_is_explicit(self):
        """Local clock interpretation must not silently create a later shutdown allowance."""
        expected = datetime(2026, 10, 27, 5, 49, tzinfo=timezone.utc).timestamp()
        self.assertEqual(deadline_timestamp('2026-10-27T05:49:00Z'), expected)
        self.assertEqual(deadline_timestamp('2026-10-27T00:49:00-05:00'), expected)
        with self.assertRaisesRegex(ValueError, 'UTC offset'):
            deadline_timestamp('2026-10-27T05:49:00')


if __name__ == '__main__':
    unittest.main()
