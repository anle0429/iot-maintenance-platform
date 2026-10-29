"""Reject changed inventories, extra payloads and altered source in the offline integration readers."""
from hashlib import sha256
from pathlib import Path
import importlib.util
import json
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('integration_evidence', ROOT / 'scripts/integration_evidence.py')
READER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(READER)


class IntegrationEvidenceIntegrityTests(unittest.TestCase):
    """Use independent tiny receipts to ensure recorded success cannot bypass byte/source checks."""
    def test_changed_payload_extra_file_and_inventory_are_rejected(self):
        """Neither adding an unrecorded result nor editing a report may preserve acceptance."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); base = root / 'docs/evidence/sample'; base.mkdir(parents=True)
            report = base / 'result.json'; report.write_bytes(b'{"passed":true}')
            raw = json.dumps({'files': {'result.json': {'bytes': 15, 'sha256': sha256(report.read_bytes()).hexdigest()}}}).encode()
            manifest = base / 'manifest.json'; manifest.write_bytes(raw); pin = sha256(raw).hexdigest()
            with patch.object(READER, 'ROOT', root):
                self.assertEqual(READER.capsule('sample', pin)[1], 1)
                extra = base / 'unrecorded.txt'; extra.write_text('not declared')
                with self.assertRaises(AssertionError): READER.capsule('sample', pin)
                extra.unlink(); report.write_bytes(b'{"passed":null}')
                with self.assertRaises(AssertionError): READER.capsule('sample', pin)
                manifest.write_bytes(b'{"files":{}}')
                with self.assertRaises(AssertionError): READER.capsule('sample', pin)

    def test_source_snapshot_and_current_source_must_agree(self):
        """A valid historical snapshot does not validate changed current code when that check is requested."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); base = root / 'docs/evidence/sample'; snapshot = base / 'run/source/tree'
            snapshot.mkdir(parents=True); (snapshot / 'code.py').write_bytes(b'print(1)\n')
            (snapshot.parent / 'manifest.json').write_text(json.dumps({'files': {'code.py': sha256(b'print(1)\n').hexdigest()}}))
            current = root / 'code.py'; current.write_bytes(b'print(1)\n')
            with patch.object(READER, 'ROOT', root):
                self.assertTrue(READER.sources(base, 'run', current=True)['files'])
                current.write_bytes(b'print(2)\n')
                with self.assertRaises(AssertionError): READER.sources(base, 'run', current=True)
                self.assertTrue(READER.sources(base, 'run', current=False)['files'])
