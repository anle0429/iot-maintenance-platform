"""Check independent source/oracle boundaries without requiring Spark or a running platform."""

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from pipelines.data_corrections_benchmark import inspect_source, normalized_record


def telemetry(identifier='A', version=1, created='2026-10-01T00:00:01Z'):
    """Provide small hand-authored sensor records with a version-specific pressure field."""
    row = {'event_id': identifier, 'machine_id': 'P-001', 'operating_status': 'operating',
           'event_ts': '2026-10-01T00:00:00Z', 'received_ts': '2026-10-01T00:00:00Z',
           'created_ts': created, 'temperature_c': 10, 'vibration_rms_mm_s': 1,
           'current_a': 2, 'rpm': 100, 'schema_version': version}
    if version == 2:
        row['pressure_bar'] = 5
    return row


def write_source(root, v1, v2):
    """Create an authenticated two-partition fixture from explicit records, without using the production generator."""
    files = []
    for version, rows in [(1, v1), (2, v2)]:
        relative = f'telemetry/schema_v{version}/part-00000.jsonl'
        path = root / relative
        path.parent.mkdir(parents=True)
        body = ''.join(json.dumps(row) + '\n' for row in rows).encode()
        path.write_bytes(body)
        files.append({'path': relative, 'bytes': len(body), 'rows': len(rows), 'sha256': sha256(body).hexdigest()})
    (root / 'manifest.json').write_text(json.dumps({'files': files}))


class DataCorrectionOracleTests(unittest.TestCase):
    """Protect the independent expected answers and reject corrupted, ambiguous or unbounded input."""

    def test_unknown_pressure_is_not_zero_and_dates_remain_exact(self):
        """Both schemas retain their meaning, and timestamp precision/time-zone conversion cannot lose a winner."""
        v1 = normalized_record(telemetry(created='2026-10-01T01:00:01.000001+01:00'))
        v2 = normalized_record(telemetry('B', 2))
        self.assertIsNone(v1['pressure_bar'])
        self.assertEqual(v2['pressure_bar'], 5.0)
        self.assertEqual(v1['created_ts'], datetime(2026, 10, 1, 0, 0, 1, 1))

    def test_latest_microsecond_and_last_source_line_win(self):
        """The oracle selects the last export even when input order first presents the newest timestamp."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            newest = telemetry(created='2026-10-01T00:00:01.000001Z')
            write_source(root, [newest, telemetry(), newest], [telemetry('B', 2)])
            inventory, records, winners = inspect_source(root)
            self.assertEqual((inventory['rows'], inventory['unique_events'], inventory['extra_exports']), (4, 2, 2))
            self.assertEqual(len(records), 4)
            answer = {row['event_id']: row for row in winners}
            self.assertEqual(answer['A']['source_line'], 3)
            self.assertEqual(answer['A']['created_ts'].microsecond, 1)
            self.assertEqual(answer['A']['source_path'], 'telemetry/schema_v1/part-00000.jsonl')

    def test_conflicting_duplicate_is_rejected_not_silently_ranked(self):
        """A changed immutable sensor measurement cannot become a harmless retry by having a later export time."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            conflict = {**telemetry(created='2026-10-01T00:00:02Z'), 'temperature_c': 99}
            write_source(root, [telemetry(), conflict], [telemetry('B', 2)])
            with self.assertRaisesRegex(ValueError, 'conflicting duplicate'):
                inspect_source(root)

    def test_checksum_mutation_fails_before_oracle_comparison(self):
        """Even valid added JSON must fail its original manifest rather than change the benchmark's input silently."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_source(root, [telemetry()], [telemetry('B', 2)])
            path = root / 'telemetry/schema_v1/part-00000.jsonl'
            path.write_bytes(path.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'differs from manifest'):
                inspect_source(root)

    def test_envelope_preserves_original_payload_path_and_line(self):
        """Dedup's untimed preparation records physical source provenance, not invented partition-local row IDs."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_source(root, [telemetry(), telemetry(created='2026-10-01T00:00:02Z')], [telemetry('B', 2)])
            envelope = root / 'envelopes.jsonl'
            inspect_source(root, envelope)
            stored = [json.loads(line) for line in envelope.read_text().splitlines()]
            self.assertEqual([row['source_line'] for row in stored], [1, 2, 1])
            self.assertEqual(json.loads(stored[1]['payload'])['created_ts'], '2026-10-01T00:00:02Z')
            self.assertEqual(stored[2]['source_path'], 'telemetry/schema_v2/part-00000.jsonl')
            with self.assertRaises(FileExistsError):
                inspect_source(root, envelope)

    def test_bad_pressure_unknown_version_and_nonfinite_values_fail(self):
        """Malformed inputs cannot appear as a schema-performance win by silently discarding values."""
        for row in [{**telemetry('B', 2), 'pressure_bar': None}, {**telemetry(), 'pressure_bar': 0},
                    {**telemetry(), 'schema_version': 3}, {**telemetry(), 'temperature_c': float('nan')}]:
            with self.subTest(row=row):
                with self.assertRaises(ValueError):
                    normalized_record(row)

    def test_large_manifest_is_rejected_without_reading_partitions(self):
        """The small study must not accidentally scan the retained 100 GB source."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_source(root, [telemetry()], [telemetry('B', 2)])
            manifest = json.loads((root / 'manifest.json').read_bytes())
            manifest['files'][0]['rows'] = 100_001
            (root / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'bounded'):
                inspect_source(root)


if __name__ == '__main__':
    unittest.main()
