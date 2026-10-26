"""Exercise immutable artifact gates against corruption, missing validation and path escapes."""

import json
from pathlib import Path
import tempfile
import unittest

from pipelines.orchestration_contract import digest, inventory, save, stage_path, verified_manifest


class ArtifactGateTests(unittest.TestCase):
    """A completed upstream may be read only when bytes and successful validation still agree."""

    def setUp(self):
        """Build tiny opaque part files; the gate checks integrity, not the Parquet parser."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = stage_path(self.root, 'known-run', 'dp1')
        part = self.directory / 'bronze/raw_telemetry/part-000.parquet'
        part.parent.mkdir(parents=True)
        part.write_bytes(b'PAR1-test-byte-fixture')
        self.part = part
        self.manifest = {'files': inventory(self.directory), 'batch': 'known-run'}
        save(self.directory / '_INGESTED.json', self.manifest)

    def validate(self, passed=True):
        """Bind a validation result to the exact immutable materialization manifest."""
        save(self.directory / 'validation.json', {'passed': passed, 'manifest_sha256': digest(self.directory / '_INGESTED.json')})

    def test_missing_and_failed_validation_block_consumption(self):
        with self.assertRaises(FileNotFoundError):
            verified_manifest(self.directory)
        self.validate(False)
        with self.assertRaisesRegex(ValueError, 'validation'):
            verified_manifest(self.directory)

    def test_successful_retry_preserves_parts(self):
        self.validate()
        before = inventory(self.directory)
        self.assertEqual(verified_manifest(self.directory), self.manifest)
        self.assertEqual(verified_manifest(self.directory), self.manifest)
        self.assertEqual(inventory(self.directory), before)

    def test_equal_length_corruption_is_rejected(self):
        self.validate()
        content = self.part.read_bytes()
        self.part.write_bytes(b'X' + content[1:])
        with self.assertRaisesRegex(ValueError, 'changed'):
            verified_manifest(self.directory)

    def test_added_or_missing_part_is_rejected(self):
        self.validate()
        extra = self.part.with_name('extra.parquet')
        extra.write_bytes(b'unexpected-part')
        with self.assertRaises(ValueError):
            verified_manifest(self.directory)
        extra.unlink()
        self.part.unlink()
        with self.assertRaises(ValueError):
            verified_manifest(self.directory)

    def test_validation_cannot_be_reused_after_manifest_change(self):
        self.validate()
        save(self.directory / '_INGESTED.json', {**self.manifest, 'batch': 'different-run'})
        with self.assertRaisesRegex(ValueError, 'different bytes'):
            verified_manifest(self.directory)

    def test_partial_directory_is_not_a_completed_stage(self):
        (self.directory / '_INGESTED.json').unlink()
        with self.assertRaises(FileNotFoundError):
            verified_manifest(self.directory, require_validated=False)

    def test_unsafe_run_ids_cannot_escape_project(self):
        for run in ('../outside', '/tmp/escape', 'Bad Run', '', 'x' * 64, None):
            with self.subTest(run=run), self.assertRaises(ValueError):
                stage_path(self.root, run, 'dp1')
        with self.assertRaises(ValueError):
            stage_path(self.root, 'safe', '../dp2')


if __name__ == '__main__':
    unittest.main()
