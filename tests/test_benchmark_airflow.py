"""Check admission rejects unsafe run IDs, changed sources and unapproved session envelopes before Spark starts."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from pipelines.benchmark_airflow import check_sources, read_spec, run_directory


class BenchmarkAdmissionTests(unittest.TestCase):
    """Exercise resource/source boundaries using temporary project-owned fixtures, without Docker or Airflow."""

    def test_traversal_and_nonstring_run_ids_rejected(self):
        """A run identifier cannot choose an arbitrary output path or executable argument."""
        for value in ['../other', '/tmp/x', 'a/b', 'x;cmd', 'A', '', None, 123]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                run_directory(Path.cwd(), value)

    def test_changed_runtime_input_rejected(self):
        """A source edit cannot silently reuse a prepared experiment's identity."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.jsonl'
            source.write_bytes(b'original\n')
            item = {'path': 'input.jsonl', 'bytes': 9, 'sha256': sha256(b'original\n').hexdigest()}
            spec = {'runtime_files': [item], 'input_files': []}
            check_sources(root, spec)
            source.write_bytes(b'modified\n')
            with self.assertRaises(ValueError):
                check_sources(root, spec)

    def test_prepared_session_limits_are_mandatory(self):
        """Matching run names alone do not authorize another machine or an unbounded cloud session."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = run_directory(root, 'test-benchmark')
            target.mkdir(parents=True)
            session = {'name': 'iot-cw-vm', 'machine_type': 'e2-highmem-8', 'max_seconds': 28800,
                       'termination_action': 'STOP', 'started_utc': datetime.now(timezone.utc).isoformat()}
            spec = {'run_id': 'test-benchmark', 'repetitions': 3, 'session': session}
            (target / 'spec.json').write_text(json.dumps(spec))
            read_spec(root, 'test-benchmark', require_admission=False)
            session['max_seconds'] = 0
            (target / 'spec.json').write_text(json.dumps(spec))
            with self.assertRaises(ValueError):
                read_spec(root, 'test-benchmark', require_admission=False)

    def test_missing_or_changed_admission_rejected(self):
        """Deleting admission or changing a prepared spec cannot authorize a heavy job."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = run_directory(root, 'test-benchmark')
            target.mkdir(parents=True)
            spec = {'run_id': 'test-benchmark', 'repetitions': 3,
                    'session': {'name': 'iot-cw-vm', 'machine_type': 'e2-highmem-8', 'max_seconds': 28800,
                                'termination_action': 'STOP', 'started_utc': datetime.now(timezone.utc).isoformat()}}
            path = target / 'spec.json'
            path.write_text(json.dumps(spec))
            with self.assertRaises(ValueError):
                read_spec(root, 'test-benchmark')
            (target / 'preflight.json').write_text(json.dumps({'passed': True, 'spec_sha256': sha256(path.read_bytes()).hexdigest()}))
            read_spec(root, 'test-benchmark')
            spec['repetitions'] = 1
            path.write_text(json.dumps(spec))
            with self.assertRaises(ValueError):
                read_spec(root, 'test-benchmark')


if __name__ == '__main__':
    unittest.main()
