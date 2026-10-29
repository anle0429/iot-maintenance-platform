"""Keep catalog canonicalization narrow and inspect screenshot headers without editing their pixels."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('scaling_evidence', ROOT / 'scripts/verify-agent-scaling.py')
evidence = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(evidence)


class ScalingEvidenceTests(unittest.TestCase):
    """Reject source changes beyond the explicitly recorded catalog newline transformation."""

    def test_catalog_allows_only_crlf_to_lf(self):
        """JSON value or other whitespace changes remain rejected even when parsing would succeed."""
        self.assertTrue(evidence.source_matches('knowledge/catalog.json', b'{"v":1}\n', b'{"v":1}\r\n'))
        self.assertFalse(evidence.source_matches('knowledge/catalog.json', b'{"v":2}\n', b'{"v":1}\r\n'))
        self.assertFalse(evidence.source_matches('knowledge/catalog.json', b'{"v": 1}\n', b'{"v":1}\r\n'))

    def test_code_and_configuration_require_exact_bytes(self):
        """The catalog exception cannot hide an implementation or chart change."""
        for name in ['scripts/benchmark-agent-scaling.py', 'infra/agents/keda.lock.json']:
            with self.subTest(name=name):
                self.assertTrue(evidence.source_matches(name, b'same\n', b'same\n'))
                self.assertFalse(evidence.source_matches(name, b'same\n', b'same\r\n'))

    def test_original_jpeg_dimensions_and_bad_magic(self):
        """Read a tiny known frame header and reject data that is not a JPEG."""
        frame = b'\xff\xd8\xff\xe0\x00\x04xx\xff\xc0\x00\x11\x08\x02\xd0\x05\x00' + b'\x00' * 12
        self.assertEqual(evidence.jpeg_dimensions(frame), (1280, 720))
        with self.assertRaises(AssertionError): evidence.jpeg_dimensions(b'not-a-jpeg')


if __name__ == '__main__': unittest.main()
