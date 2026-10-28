"""Test Vault transport restrictions and secret-free errors before native policy/consumer verification."""
from pathlib import Path
import ssl,tempfile,unittest
from unittest.mock import patch
from urllib.error import HTTPError,URLError
from infra.security.vault_client import VaultClient,VaultRequestError

ROLE={'role_id':'a'*8+'-'+ 'a'*4+'-'+ 'a'*4+'-'+ 'a'*4+'-'+ 'a'*12,'secret_id':'b'*8+'-'+ 'b'*4+'-'+ 'b'*4+'-'+ 'b'*4+'-'+ 'b'*12}
class VaultClientTests(unittest.TestCase):
    """Reject unsafe endpoints and redact failures; native proof separately checks real TLS/auth/policy."""
    def client(self):
        with patch('infra.security.vault_client.ssl.create_default_context',return_value=ssl.create_default_context()):return VaultClient('https://10.52.0.1:18200','unused',ROLE)
    def test_unsafe_endpoint_partitions_and_credential_shape(self):
        for value in ['http://10.52.0.1:18200','https://8.8.8.8','https://127.0.0.1','https://0.0.0.0','https://localhost','https://u:p@10.52.0.1','https://10.52.0.1/a','https://10.52.0.1?token=x','https://10.52.0.1#x']:
            with self.subTest(value=value),self.assertRaises(ValueError):VaultClient(value,'unused',ROLE)
        with self.assertRaises(ValueError):VaultClient('https://10.52.0.1','unused',{**ROLE,'token':'sensitive'})
    def test_secret_bearing_errors_are_redacted(self):
        c=self.client()
        for error,kind in [(HTTPError('secret-url',403,'canary-token',{},None),VaultRequestError),(HTTPError('secret-url',307,'canary-token',{'Location':'http://8.8.8.8'},None),VaultRequestError),(URLError('canary-password'),RuntimeError)]:
            with patch.object(c,'_open',side_effect=error),self.assertRaises(kind) as result:c.login()
            self.assertNotIn('canary',str(result.exception));self.assertNotIn('secret-url',str(result.exception))
    def test_cannot_read_without_login_or_escape_kv_path(self):
        c=self.client()
        with self.assertRaises(RuntimeError):c.read('iot/data/ci/airflow')
        for path in ['iot/data/ci/../gateway/basic','iot/data/ci/airflow?version=1','sys/raw','iot/data/ci/airflow/x']:
            with self.subTest(path=path),self.assertRaises(ValueError):c.read(path)
        self.assertNotIn(ROLE['secret_id'],repr(c))

if __name__=='__main__':unittest.main()
