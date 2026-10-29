"""Ensure original inference measurements cannot be silently changed or accompanied by a rewritten manifest."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('llm_inference_evidence', ROOT / 'scripts/verify-llm-inference.py')
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)


class NativeInferenceEvidenceTests(unittest.TestCase):
    """Check complete original proof and reject independent payload/summary/manifest tampering."""
    def test_original_native_capsule_verifies(self):
        """The source-bound native capsule must pass with a scoped tool-selection claim."""
        report = MODULE.verify()
        self.assertTrue(report['passed']); self.assertEqual(report['direct_measured_requests'], 18)
        self.assertIn('not MCP execution', report['scope'])

    def test_changed_payload_and_rewritten_manifest_are_rejected(self):
        """Neither a byte change nor editing the accompanying hash inventory may hide a changed original."""
        for alter_manifest in [False, True]:
            with self.subTest(alter_manifest=alter_manifest), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'evidence'; shutil.copytree(MODULE.BASE, target)
                if alter_manifest:
                    manifest = json.loads((target / 'manifest.json').read_bytes()); manifest['capture_note'] = 'changed'
                    (target / 'manifest.json').write_text(json.dumps(manifest))
                else:
                    path = target / 'native/llm-foundation-29b/tool-response.json'; path.write_bytes(path.read_bytes() + b'\n')
                with patch.object(MODULE, 'BASE', target), self.assertRaises(AssertionError): MODULE.verify()
