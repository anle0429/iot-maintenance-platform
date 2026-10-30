"""Check the administrative relay's credential boundary against foreign origins and proxy/path confusion."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('mcp_gateway_view', Path(__file__).resolve().parents[1]/'scripts/view-mcp-gateway.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class ViewerBoundaryTests(unittest.TestCase):
    """Keep the local viewer from becoming a credential-bearing general proxy."""

    def test_only_fixed_local_authorities_and_same_origin(self):
        """Allow native Swagger and its same-origin actions while denying another site's browser requests."""
        self.assertTrue(module.allowed_request('127.0.0.1:19111', '', '/docs', 'GET'))
        self.assertTrue(module.allowed_request('localhost:19111', 'http://localhost:19111', '/v1/knowledge:search', 'POST'))
        self.assertTrue(module.allowed_request('127.0.0.1:21947', 'http://127.0.0.1:21947', '/docs', 'GET', port=21947))
        self.assertFalse(module.allowed_request('127.0.0.1:19111', '', '/docs', 'GET', port=21947))
        for host, origin in [('localhost:19112', ''), ('127.0.0.1:19111.evil', ''), ('127.0.0.1:19111', 'https://foreign.invalid'), ('127.0.0.1:19111', 'null')]:
            with self.subTest(host=host, origin=origin): self.assertFalse(module.allowed_request(host, origin, '/docs', 'GET'))

    def test_reject_aliases_and_general_proxy_targets(self):
        """Deny encoded traversal, authority URLs, queries and endpoints that could expose administration or other roles."""
        for path in ['https://foreign.invalid/docs', '//foreign.invalid/docs', '/docs?target=https://foreign.invalid', '/docs#fragment', '/%64ocs', '/v1/machines/P-001/../features', '/v1/machines/P-001%2ffeatures', '/v1/drift:assess', '/metrics', '/mcp', '/admin', '/docs\\anything']:
            with self.subTest(path=path): self.assertFalse(module.allowed_request('127.0.0.1:19111', '', path, 'GET'))

    def test_read_and_prediction_methods_are_explicit(self):
        """Permit existing structured operations without arbitrary verbs, arbitrary machine IDs or remote management."""
        for method, path in [('GET', '/openapi.json'), ('HEAD', '/health/live'), ('GET', '/v1/machines/P-001/features'), ('POST', '/v1/machines/P-001:predict')]:
            self.assertTrue(module.allowed_request('127.0.0.1:19111', '', path, method))
        for method, path in [('DELETE', '/docs'), ('POST', '/docs'), ('PUT', '/v1/knowledge:search'), ('GET', '/v1/machines/arbitrary/features'), ('POST', '/v1/machines/P-01:predict')]:
            self.assertFalse(module.allowed_request('127.0.0.1:19111', '', path, method))


if __name__ == '__main__': unittest.main()
