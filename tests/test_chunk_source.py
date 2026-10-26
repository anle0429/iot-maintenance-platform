"""Check source/receipt binding, integrity, immutable reports and offline-only catalog access."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from generator.chunked import ChunkConfig, generate_chunked, digest_file
from generator.storage import write_json
from pipelines.chunk_source import ChunkSource, outside_source, validation_receipt


class ChunkSourceTests(unittest.TestCase):
    """Validate the host adapter without requiring Spark, Docker or in-memory large datasets."""

    def setUp(self):
        """Use a small source with both firmware versions, retry and late delivery behavior."""
        self.config=ChunkConfig(machine_count=2,duration_hours=1,offline_duplicate_rate=0.1,
                                stream_duplicate_rate=0.05,late_rate=0.1)

    def test_receipt_binds_exact_manifest_and_table_bytes(self):
        """A valid receipt and verified source expose the expected offline records and part sequence."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            report=Path(directory)/'receipt.json'
            manifest=generate_chunked(self.config,root,chunk_rows=7)
            receipt=validation_receipt(root,report)
            source=ChunkSource(root,report)
            self.assertEqual(source.manifest_sha256,digest_file(root/'manifest.json'))
            self.assertEqual(receipt['quality']['unique_telemetry_rows'],120)
            self.assertEqual(sum(source.manifest['tables'][t]['rows'] for t in ('v1','v2')),132)
            self.assertEqual(len(list(source.paths('v1'))),manifest['tables']['v1']['parts'])
            source.unchanged()

    def test_stream_is_excluded_even_when_fixture_was_generated(self):
        """Batch ingest must not accidentally count stream copies as additional offline telemetry."""
        with tempfile.TemporaryDirectory() as directory:
            root,report=Path(directory)/'source',Path(directory)/'receipt.json'
            generate_chunked(self.config,root)
            validation_receipt(root,report)
            source=ChunkSource(root,report)
            for method in (source.directory,lambda table:list(source.paths(table))):
                with self.assertRaisesRegex(ValueError,'only offline'):
                    method('stream')

    def test_receipt_for_another_seed_is_rejected(self):
        """Matching counts are insufficient when the source manifest/config differs."""
        with tempfile.TemporaryDirectory() as directory:
            left,right=Path(directory)/'left',Path(directory)/'right'
            report=Path(directory)/'receipt.json'
            generate_chunked(self.config,left)
            generate_chunked(replace(self.config,seed=43),right)
            validation_receipt(left,report)
            with self.assertRaisesRegex(ValueError,'different source manifest'):
                ChunkSource(right,report)

    def test_changed_part_after_validation_invalidates_receipt(self):
        """The Spark-side adapter rehashes actual bytes rather than trusting an old passed flag."""
        with tempfile.TemporaryDirectory() as directory:
            root,report=Path(directory)/'source',Path(directory)/'receipt.json'
            generate_chunked(self.config,root)
            validation_receipt(root,report)
            part=root/'telemetry/schema_v1/part-000000.jsonl'
            part.write_bytes(part.read_bytes()+b'{}\n')
            with self.assertRaisesRegex(ValueError,'part integrity'):
                ChunkSource(root,report)

    def test_source_changed_after_open_is_detected(self):
        """Post-ingest integrity checks catch mutation of a previously opened source."""
        with tempfile.TemporaryDirectory() as directory:
            root,report=Path(directory)/'source',Path(directory)/'receipt.json'
            generate_chunked(self.config,root)
            validation_receipt(root,report)
            source=ChunkSource(root,report)
            part=root/'telemetry/schema_v2/part-000000.jsonl'
            part.write_bytes(part.read_bytes()+b'{}\n')
            with self.assertRaisesRegex(ValueError,'part integrity'):
                source.unchanged()

    def test_receipt_validator_version_must_match(self):
        """Receipts from an older validator cannot silently certify a changed validation protocol."""
        with tempfile.TemporaryDirectory() as directory:
            root,report=Path(directory)/'source',Path(directory)/'receipt.json'
            generate_chunked(self.config,root)
            result=validation_receipt(root,report)
            result['validator_sha256'][0]['sha256']='0'*64
            write_json(report,result)
            with self.assertRaisesRegex(ValueError,'validator version'):
                ChunkSource(root,report)

    def test_source_and_existing_receipt_are_preserved(self):
        """Report guards must run before any source or evidence file is overwritten."""
        with tempfile.TemporaryDirectory() as directory:
            root,report=Path(directory)/'source',Path(directory)/'receipt.json'
            generate_chunked(self.config,root)
            validation_receipt(root,report)
            before=report.read_bytes()
            with self.assertRaisesRegex(ValueError,'receipt exists'):
                validation_receipt(root,report)
            self.assertEqual(report.read_bytes(),before)
            with self.assertRaisesRegex(ValueError,'outside'):
                validation_receipt(root,root/'manifest.json')
            with self.assertRaisesRegex(ValueError,'outside'):
                outside_source(root,root/'nested'/'output')

    def test_incomplete_generation_cannot_be_opened(self):
        """A directory without a generation marker is not an ingestible source."""
        with tempfile.TemporaryDirectory() as directory:
            root,report=Path(directory)/'source',Path(directory)/'receipt.json'
            generate_chunked(self.config,root)
            validation_receipt(root,report)
            (root/'_GENERATION_SUCCESS.json').unlink()
            with self.assertRaises(OSError):
                ChunkSource(root,report)


if __name__=='__main__':
    unittest.main()
