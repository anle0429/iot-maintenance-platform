"""Check that VM recovery cannot replace the LLM reader with an older unrelated metrics identity."""
from pathlib import Path
import base64
import json
import unittest
from infra.session_tokens import discovery_identity


class DiscoveryIdentityTests(unittest.TestCase):
    """Exercise the allowed identities and rejection boundaries without using real credentials."""
    def test_identity_boundaries(self):
        """Each credential path must retain its own subject, including when its token has expired."""
        root = Path('runtime').resolve()
        for path, identity in [('api-observability-28b/prometheus-auth/token', 'iot-api-metrics'),
                               ('log-discovery-28/auth/token', 'iot-api-metrics'),
                               ('llm-monitoring-29a/auth/token', 'iot-llm-metrics-reader')]:
            payload = base64.urlsafe_b64encode(json.dumps({'sub': 'system:serviceaccount:iot-observability:' + identity, 'exp': 1}).encode()).rstrip(b'=')
            raw = b'header.' + payload + b'.signature\n'
            with self.subTest(path=path):
                self.assertEqual(discovery_identity(root, root / path, raw), identity)
                other = 'llm-monitoring-29a/auth/token' if identity == 'iot-api-metrics' else 'log-discovery-28/auth/token'
                with self.assertRaises(ValueError): discovery_identity(root, root / other, raw)
                with self.assertRaises(ValueError): discovery_identity(root, root / 'other/token', raw)

    def test_invalid_retained_token(self):
        """A malformed credential must fail before any issuance or file write."""
        for raw in [b'', b'not-a-token', b'a.invalid.b', b'a.e30.b']:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError): discovery_identity('runtime', 'runtime/log-discovery-28/auth/token', raw)
