"""Exercise the native Vault viewer's secret-value, mutation, origin and alias boundaries."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('vault_metadata_view', Path(__file__).resolve().parents[1]/'scripts/view-vault-metadata.py')
VIEW = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(VIEW)


class MetadataViewerTests(unittest.TestCase):
    """Keep an administrative UI view from exposing credentials or changing retained Vault state."""

    def test_original_ui_compression_is_preserved(self):
        """Use the native nonempty gzip representation without recompressing or modifying response bytes."""
        self.assertEqual(VIEW.upstream_encoding('/ui/'), 'gzip')
        self.assertEqual(VIEW.upstream_encoding('/ui/assets/vault.js'), 'gzip')
        self.assertEqual(VIEW.upstream_encoding('/v1/iot/metadata/agents/gateway'), 'identity')

    def test_native_ui_and_dedicated_metadata(self):
        """Admit original UI resources, own-token inspection and exactly the required KV metadata."""
        for path in ['/ui/', '/ui/assets/vault.js', '/v1/auth/token/lookup-self', '/v1/sys/internal/ui/mounts',
                     '/v1/iot/metadata/agents?list=true', '/v1/iot/metadata/agents/gateway', '/v1/iot/metadata/agents/services']:
            with self.subTest(path=path): self.assertTrue(VIEW.allowed_request('127.0.0.1:21950', '', path, 'GET'))

    def test_all_values_and_mutations_are_denied(self):
        """Even the existing operator login cannot forward secret-value reads, token revocation or policy changes."""
        for method, path in [('GET', '/v1/iot/data/agents/gateway'), ('GET', '/v1/iot/data/agents/services'),
                             ('GET', '/v1/iot/metadata/ci/airflow'), ('POST', '/v1/auth/token/revoke-self'),
                             ('POST', '/v1/iot/metadata/agents/gateway'), ('PUT', '/v1/sys/policy/root'),
                             ('DELETE', '/v1/iot/metadata/agents/services'), ('POST', '/ui/')]:
            with self.subTest(method=method, path=path): self.assertFalse(VIEW.allowed_request('127.0.0.1:21950', '', path, method))

    def test_foreign_origins_and_ambiguous_paths(self):
        """Reject encoded aliases, traversal, proxy URLs and alternate hosts before forwarding authentication."""
        for target in ['//foreign.invalid/ui/', 'https://foreign.invalid/ui/', '/ui/../v1/iot/data/agents/services',
                       '/v1/iot/%64ata/agents/gateway', '/v1/iot/metadata/agents?list=true&list=true', '/ui\\assets', '/ui/#fragment']:
            with self.subTest(target=target): self.assertFalse(VIEW.allowed_request('127.0.0.1:21950', '', target, 'GET'))
        self.assertFalse(VIEW.allowed_request('foreign.invalid', '', '/ui/', 'GET'))
        self.assertFalse(VIEW.allowed_request('127.0.0.1:21950', 'https://foreign.invalid', '/ui/', 'GET'))

    def test_only_permission_lookup_body_is_allowed(self):
        """Allow permission inspection without granting token creation or accepting arbitrary administrative payloads."""
        self.assertTrue(VIEW.allowed_request('localhost:21950', 'http://localhost:21950', '/v1/sys/capabilities-self', 'POST'))
        self.assertTrue(VIEW.allowed_body(b'{"paths":["iot/data/agents/gateway","iot/metadata/agents/services"]}'))
        for raw in [b'null', b'[]', b'{}', b'{"paths":[]}', b'{"paths":["sys/policy/root"]}', b'{"paths":["iot/metadata"],"token":"x"}']:
            with self.subTest(raw=raw): self.assertFalse(VIEW.allowed_body(raw))


if __name__ == '__main__': unittest.main()
