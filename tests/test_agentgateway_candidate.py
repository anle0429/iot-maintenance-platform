"""Check the candidate resource boundary and the pinned proxy against loopback protocol fixtures."""

from contextlib import ExitStack
from hashlib import sha256
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
import json
import os
import socket
import subprocess
import unittest

from infra.agentgateway.candidate import NAME, PORTS, a2a_path, bearer_hash, config, manifests
from infra.agentgateway.probe import GatewayProbe, TOOLS


WINDOWS_DIGEST = '909b93dc8fb5486cc0418a818b4a28fa60046cbd619013e33d1e4b20b889ed3c'
LINUX_DIGEST = '1b2fc01a2de29ca113bbb6258b5cae7b1eb3c724071c09d687a449132f2f42f8'
TOKENS = {role: role + '-fixture-' + 'a' * 32 for role in PORTS}
MODEL_BACKEND_TOKEN = 'model-backend-fixture-' + 'b' * 32
OBSERVER_REQUEST_ID = '0123456789abcdef0123456789abcdef'


class BackendFixture(BaseHTTPRequestHandler):
    """Represent the wire contracts only; never claim these bodies are model or service evidence."""

    def log_message(self, _format, *_args):
        """Keep this test quiet and avoid printing request headers or bodies."""

    def send(self, status, value=None, observer_request_id=None):
        """Return a bounded JSON or notification response to the real gateway."""
        body = json.dumps(value).encode() if value is not None else b''
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        if observer_request_id is not None:
            self.send_header('X-Iot-Request-Id', observer_request_id)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """Require the fixed A2A identity, then expose a v0.3 fixture card."""
        if self.server.role == 'a2a' and self.path.endswith('/.well-known/agent-card.json'):
            if self.headers.get('X-User-Id') != 'coursework' or self.headers.get('Authorization'):
                self.send(403)
                return
            self.send(200, {'name': 'iot_coordinator', 'description': 'fixture only', 'version': '0.0.0',
                            'protocolVersion': '0.3.0', 'url': 'http://127.0.0.1:1234/',
                            'capabilities': {}, 'defaultInputModes': ['text/plain'],
                            'defaultOutputModes': ['text/plain'], 'skills': []})
        else:
            self.send(404)

    def do_POST(self):
        """Verify the credential boundary and mirror each protocol's actual response shape."""
        role = self.server.role
        value = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        if role == 'model' and self.path == '/v1/chat/completions':
            if self.headers.get_all('Authorization') != ['Bearer ' + MODEL_BACKEND_TOKEN]:
                self.send(401)
                return
            marker = OBSERVER_REQUEST_ID if value.get('model') != 'invalid-marker' else 'invalid-header-value'
            self.send(200, {'model': value['model'], 'choices': [{'message': {'content': 'fixture only'}}],
                            'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3}}, marker)
            return
        if (role == 'a2a' and self.path == a2a_path('coordinator')
                and self.headers.get('X-User-Id') == 'coursework' and not self.headers.get('Authorization')):
            if value['method'] != 'message/send' or self.headers.get('A2A-Version') != '0.3':
                self.send(400)
                return
            self.send(200, {'jsonrpc': '2.0', 'id': value['id'], 'result': {'id': 'fixture-task',
                            'contextId': 'fixture-context', 'status': {'state': 'completed'}}})
            return
        if role not in TOOLS or self.path != '/mcp' or self.headers.get('Authorization') != 'Bearer ' + TOKENS[role]:
            self.send(403)
            return
        method = value.get('method')
        if 'id' not in value:
            self.send(202)
            return
        if method == 'initialize':
            result = {'protocolVersion': value['params']['protocolVersion'], 'capabilities': {'tools': {}},
                      'serverInfo': {'name': role, 'version': '0.0.0'}}
        elif method == 'tools/list':
            result = {'tools': [{'name': name, 'description': 'fixture tool',
                                'inputSchema': {'type': 'object'}} for name in sorted(TOOLS[role])]}
        elif method == 'tools/call' and value['params']['name'] in TOOLS[role]:
            result = {'content': [{'type': 'text', 'text': 'fixture only'}], 'isError': False}
        else:
            self.send(200, {'jsonrpc': '2.0', 'id': value['id'], 'error': {'code': -32601, 'message': 'not found'}})
            return
        self.send(200, {'jsonrpc': '2.0', 'id': value['id'], 'result': result})


def unused_port():
    """Choose a free local test port without reserving a machine-wide fixed port."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class AgentgatewayCandidateTest(unittest.TestCase):
    """Exercise route isolation and genuine proxy behavior independently of the cloud deployment."""

    def test_private_resources_and_key_separation(self):
        """Prevent public exposure, a cross-role allow rule or shared credentials in the candidate."""
        hashes = {role: bearer_hash(token) for role, token in TOKENS.items()}
        objects = manifests(hashes, 'agw-test')
        service = next(item['spec'] for item in objects if item['kind'] == 'Service')
        self.assertEqual(service['type'], 'ClusterIP')
        deployment = next(item['spec'] for item in objects if item['kind'] == 'Deployment')
        self.assertFalse(deployment['template']['spec']['automountServiceAccountToken'])
        volumes = deployment['template']['spec']['volumes']
        credentials = next(volume['projected'] for volume in volumes if volume['name'] == 'backend-credentials')
        self.assertIn({'secret': {'name': 'iot-local-model-client',
                                  'items': [{'key': 'API_KEY', 'path': 'model'}]}}, credentials['sources'])
        policy = next(item['spec'] for item in objects if item['kind'] == 'NetworkPolicy' and item['metadata']['name'] == NAME)
        for role in ('retrieval', 'drift'):
            rule = next(rule for rule in policy['ingress'] if rule['ports'][0]['port'] == PORTS[role])
            self.assertIn({'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'iot-agents'}},
                           'podSelector': {'matchLabels': {'ate.dev/worker-pool': 'iot-' + role + '-pool'}}},
                          rule['from'])
            self.assertNotIn({'ate.dev/worker-pool': 'iot-coordinator-pool'},
                             [source['podSelector']['matchLabels'] for source in rule['from']])
        self.assertTrue(all(token not in json.dumps(objects) for token in TOKENS.values()))
        with self.assertRaises(ValueError):
            manifests({role: hashes['model'] for role in PORTS}, 'agw-test')

    @unittest.skipUnless(os.environ.get('IOT_AGENTGATEWAY_BINARY'), 'set IOT_AGENTGATEWAY_BINARY to the verified release')
    def test_pinned_binary_routes_protocol_fixtures(self):
        """Send actual HTTP and MCP/A2A RPCs through the official pinned executable."""
        binary = Path(os.environ['IOT_AGENTGATEWAY_BINARY']).resolve()
        self.assertEqual(sha256(binary.read_bytes()).hexdigest(), WINDOWS_DIGEST if os.name == 'nt' else LINUX_DIGEST)
        with TemporaryDirectory() as name, ExitStack() as stack:
            out = Path(name)
            (out / 'model').write_text(MODEL_BACKEND_TOKEN)
            backends = {}
            for role in PORTS:
                backend = ThreadingHTTPServer(('127.0.0.1', 0), BackendFixture)
                backend.role = role
                thread = Thread(target=backend.serve_forever, daemon=True)
                thread.start()
                stack.callback(backend.server_close)
                stack.callback(backend.shutdown)
                backends[role] = f'127.0.0.1:{backend.server_port}'
                if role in TOOLS:
                    (out / role).write_text(TOKENS[role])
            ports = {role: unused_port() for role in PORTS}
            ready = unused_port()
            value = config({role: bearer_hash(token) for role, token in TOKENS.items()}, backends=backends,
                           ports=ports, credential_dir=out.as_posix(), local=True)
            value['config'].update({'adminAddr': f'127.0.0.1:{unused_port()}',
                                    'readinessAddr': f'127.0.0.1:{ready}', 'statsAddr': f'127.0.0.1:{unused_port()}'})
            path = out / 'config.json'
            path.write_text(json.dumps(value))
            subprocess.run([binary, '--validate-only', '-f', path], check=True, timeout=15, capture_output=True)
            environment = {name: value for name, value in os.environ.items() if name.upper() != 'RUST_LOG'}
            with (out / 'gateway.log').open('wb') as log:
                with subprocess.Popen([binary, '-f', path], cwd=out, env=environment,
                                      stdout=log, stderr=log) as gateway:
                    try:
                        deadline = monotonic() + 15
                        while monotonic() < deadline and gateway.poll() is None:
                            conn = HTTPConnection('127.0.0.1', ready, timeout=1)
                            try:
                                conn.request('GET', '/healthz/ready')
                                if conn.getresponse().status == 200:
                                    break
                            except OSError:
                                sleep(0.1)
                            finally:
                                conn.close()
                        else:
                            self.fail('Official gateway did not become ready: ' + (out / 'gateway.log').read_text()[-2400:])
                        probe = GatewayProbe('127.0.0.1', TOKENS, out / 'receipts', ports)
                        probe.model()
                        status, _, _ = probe.request('invalid-observer-id', 'model', '/v1/chat/completions',
                                                      method='POST', payload={'model': 'invalid-marker'})
                        self.assertEqual(status, 200)
                        probe.mcp('retrieval', {'machine_id': 'P-001'})
                        probe.mcp('drift', {'window_id': 'fixture', 'machine_ids': ['P-001']})
                        probe.a2a('coordinator', 'Fixture protocol check only.')
                        request = json.loads((out / 'receipts' / 'coordinator-a2a-request.json').read_text())
                        path = a2a_path('coordinator')
                        for label, endpoint in [('canonical', path), ('slash', path + '/')]:
                            status, _, headers = probe.request(label + '-path', 'a2a', endpoint,
                                method='POST', payload=request, headers={'A2A-Version': '0.3'})
                            self.assertEqual(status, 200)
                            self.assertEqual(headers.get('x-iot-agentgateway-route'), 'a2a-coordinator')
                        probe.reject('unauthenticated-slash-path', 'a2a', path + '/', method='POST', payload=request)
                        status, _, _ = probe.request('unexpected-a2a-path', 'a2a', a2a_path('coordinator') + '/extra',
                            method='POST', payload=request)
                        self.assertEqual(status, 404)
                        status, _, _ = probe.request('unexpected-path', 'model', '/api/me')
                        self.assertEqual(status, 404)
                    finally:
                        gateway.terminate()
                        gateway.wait(timeout=10)
            access = (out / 'gateway.log').read_text(encoding='utf-8')
            self.assertTrue(any('observer_request_id' in line and OBSERVER_REQUEST_ID in line
                                for line in access.splitlines()), access[-2400:])
            self.assertNotIn('invalid-header-value', access)
            for token in [*TOKENS.values(), MODEL_BACKEND_TOKEN]:
                self.assertNotIn(token, access)


if __name__ == '__main__':
    unittest.main()
