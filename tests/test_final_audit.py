"""Ensure final-rubric auditing rejects weakened requirements, missing rows and modified or escaped evidence."""
import importlib.util
import json
import subprocess
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('final_audit', ROOT / 'scripts/audit-final-coursework.py')
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class FinalAuditTests(unittest.TestCase):
    """Use original requirements and tiny independent capsule fixtures rather than reproduce verifier internals."""
    def setUp(self):
        """Read immutable source cells and the current technical tracker without changing either."""
        self.snapshot = json.loads((ROOT / 'docs/evidence/rubric-final/source.json').read_bytes())
        self.text = (ROOT / 'docs/final-rubric-tracker.md').read_text(encoding='utf-8')

    def test_original_rows_and_merged_source_cells(self):
        """Resolve TTL and offline-push rows to the same original merged deliverable while keeping distinct requirements."""
        rows = AUDIT.rubric_rows(self.snapshot, self.text)
        self.assertEqual(len(rows), 57)
        self.assertEqual(rows['F20'][1], 'C20; D19; E20')
        self.assertEqual(rows['F22'][1], 'C22; D19; E22')
        self.assertEqual(rows['F20'][5], '1')
        self.assertIn('OFFLINE store', rows['F20'][3])
        # Green status text is not evidence of success and is not used to accept the requirements.
        AUDIT.rubric_rows(self.snapshot, self.text.replace('Recorded ML evidence', 'Not assessed'))

    def test_english_translation_cannot_be_weakened_with_the_tracker(self):
        """Reject editing the canonical English threshold as well as the rendered table."""
        source = ROOT / 'docs/rubric/final-ml-english.json'
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / 'translation.json'
            altered.write_bytes(source.read_bytes().replace(b'Mutation score > 80%', b'Mutation score > 1%'))
            with self.assertRaises(ValueError): AUDIT.english_view(altered, 'ML')

    def test_llm_rows_preserve_source_addresses_and_open_scope(self):
        """Check all sixty LLM requirements without treating shared ML proof as an agent deployment."""
        source = json.loads((ROOT / 'docs/evidence/rubric-llm/source.json').read_bytes())
        text = (ROOT / 'docs/final-llm-rubric-tracker.md').read_text(encoding='utf-8')
        rows = AUDIT.rubric_rows(source, text, 'LLM')
        self.assertEqual(len(rows), 60)
        self.assertEqual(rows['L06'][1], 'A6; D6; E6')
        self.assertIn('Open', rows['L03'][6])
        self.assertIn('TTFT', rows['L54'][3])

    def test_snapshot_pin_matches_git_blob_and_checkout(self):
        """Catch a Windows CRLF-derived pin that cannot verify an otherwise clean independent checkout."""
        path = 'docs/evidence/rubric-final/source.json'
        blob = subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT)
        self.assertEqual(sha256(blob).hexdigest(), AUDIT.SOURCE_SNAPSHOT_SHA256)
        self.assertEqual((ROOT / path).read_bytes(), blob)
        self.assertEqual(AUDIT.original_snapshot(ROOT / path), self.snapshot)

    def test_weakened_requirement_missing_and_duplicate_rows_fail(self):
        """Reject a lowered mutation threshold, a removed final idea and a duplicated API criterion."""
        line3 = next(line for line in self.text.splitlines() if line.startswith('| F03 |'))
        for text in [self.text.replace('Mutation score > 80%', 'Mutation score > 1%'),
                     '\n'.join(line for line in self.text.splitlines() if not line.startswith('| F59 |')),
                     self.text + '\n' + line3]:
            with self.subTest(text_length=len(text)), self.assertRaises(AssertionError):
                AUDIT.rubric_rows(self.snapshot, text)

    def test_original_snapshot_cannot_be_edited_together_with_tracker(self):
        """Changing the purported source workbook threshold must fail even before comparing tracker rows."""
        with tempfile.TemporaryDirectory() as directory:
            changed = dict(self.snapshot); changed['source_point_total'] = 99
            path = Path(directory) / 'source.json'; path.write_text(json.dumps(changed), encoding='utf-8')
            with self.assertRaises(ValueError): AUDIT.original_snapshot(path)

    def test_payload_hash_and_repository_boundary(self):
        """A changed report and a valid hash pointing outside docs/evidence must both fail."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); base = root / 'docs/evidence/sample'; capture = base / 'capture'
            capture.mkdir(parents=True); payload = base / 'result.json'; payload.write_bytes(b'{"passed":true}')
            pin = sha256(payload.read_bytes()).hexdigest()
            (capture / 'manifest.json').write_text(json.dumps({'native/result.json': pin}))
            mapping = {'native/result.json': {'path': 'docs/evidence/sample/result.json', 'sha256': pin}}
            (capture / 'evidence-map.json').write_text(json.dumps(mapping))
            self.assertEqual(AUDIT.capsule_manifest(root, 'sample')['verified_payloads'], 1)
            payload.write_bytes(b'{"passed":false}')
            with self.assertRaises(AssertionError): AUDIT.capsule_manifest(root, 'sample')
            outside = root / 'outside.json'; outside.write_bytes(b'{"passed":true}')
            mapping['native/result.json']['path'] = 'outside.json'
            (capture / 'evidence-map.json').write_text(json.dumps(mapping))
            with self.assertRaises(AssertionError): AUDIT.capsule_manifest(root, 'sample')


if __name__ == '__main__':
    unittest.main()
