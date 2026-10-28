"""Check discovery trust boundaries and a bounded real-Pod experiment before native Alloy validation."""
import unittest
from ml.log_discovery import application_discovery,probe_deployment,render_discovery
from tests.test_log_monitoring import observed


class LogDiscoveryTests(unittest.TestCase):
    def test_explicit_namespace_node_tls_and_no_secret_discovery(self):
        config=application_discovery('/srv/private/token','/srv/private/ca.crt','iot-cw-vm')
        self.assertIn('names = ["iot-features", "iot-serving"]',config);self.assertIn('spec.nodeName=iot-cw-vm',config)
        self.assertNotIn('insecure_skip_verify',config);self.assertNotIn('kubeconfig',config);self.assertIn('/var/log/pods/${1}_${2}_${3}/${4}/*.log',config)
        self.assertIn('regex = "api|kserve-container|probe"',config);self.assertNotIn('role = "secret"',config)

    def test_untrusted_paths_or_node_are_rejected(self):
        for token,ca,node in [('/srv/../token','/srv/ca','node'),('/srv/token','/srv/token','node'),('/srv/token','/srv/ca','node"\nrole="secret'),('/srv/*/token','/srv/ca','node')]:
            with self.subTest(node=node),self.assertRaises(ValueError):application_discovery(token,ca,node)

    def test_preserves_host_parsing_and_replaces_fixed_pod_paths(self):
        docs=render_discovery('/srv/runtime','/srv/coursework',observed(),'/srv/private/token','/srv/private/ca','node')
        self.assertEqual(docs['alloy'].count('action_on_duplicate_timestamp = "keep"'),3)
        self.assertNotIn('12345678-1234-1234-1234-123456789abc',docs['alloy']);self.assertIn('stage.cri {}',docs['alloy'])
        self.assertEqual(docs['dashboard']['uid'],'iot-linux-logs');self.assertEqual(len(docs['sources']['host_files']),3)

    def test_probe_batches_change_pod_template_without_credentials(self):
        first=probe_deployment('first');second=probe_deployment('second');self.assertNotEqual(first['spec']['template'],second['spec']['template'])
        spec=first['spec']['template']['spec'];self.assertFalse(spec['automountServiceAccountToken']);self.assertNotIn('volumes',spec);self.assertEqual(spec['securityContext']['seccompProfile'],{'type':'RuntimeDefault'})
        self.assertEqual(spec['containers'][0]['resources']['limits']['memory'],'128Mi')
        with self.assertRaises(ValueError):probe_deployment('arbitrary')


if __name__=='__main__':unittest.main()
