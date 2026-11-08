"""Keep optional experiment ingress bounded while exercising the actual pinned proxy."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import inspect
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
import unittest

from infra.agentgateway.candidate import PORTS, bearer_hash, config, manifests
from infra.agentgateway.probe import GatewayProbe, rpc
from tests.test_agentgateway_candidate import (
    BackendFixture, TOKENS, WINDOWS_DIGEST, LINUX_DIGEST, unused_port,
)

AB_PATHS = {variant: '/api/a2a-sandboxes/iot-agents/iot-ab-retrieval-' + variant
            for variant in ('a', 'b')}
DELEGATE_TOKEN = 'delegation-fixture-' + 'd' * 32


class AgentABGatewayRouteTests(unittest.TestCase):
    """An A/B replay adds only its two explicit authenticated operator endpoints."""

    def test_optional_routes_preserve_all_existing_routes_and_ports(self):
        """No model, MCP, production-agent, delegate, listener or tracing field is replaced."""
        self.assertIn('ab_operator_routes', inspect.signature(config).parameters)
        hashes = {role: bearer_hash(token) for role, token in TOKENS.items()}
        for delegation in (None, bearer_hash(DELEGATE_TOKEN)):
            for tracing in (False, True):
                baseline = config(hashes, tracing=tracing, delegation_hash=delegation)
                selected = config(hashes, tracing=tracing, delegation_hash=delegation,
                                  ab_operator_routes=True)
                restored = deepcopy(selected)
                routes = next(bind['listeners'][0]['routes'] for bind in restored['binds']
                              if bind['port'] == PORTS['a2a'])
                extra = [route for route in routes if route['name'].startswith('a2a-ab-')]
                self.assertEqual({route['name'] for route in extra}, {
                    'a2a-ab-retrieval-a', 'a2a-ab-retrieval-a-slash',
                    'a2a-ab-retrieval-b', 'a2a-ab-retrieval-b-slash'})
                routes[:] = [route for route in routes if route not in extra]
                self.assertEqual(restored, baseline)
                for route in extra:
                    variant = 'a' if '-a' == route['name'].removesuffix('-slash')[-2:] else 'b'
                    path = AB_PATHS[variant]
                    expected = [('POST', path + '/')] if route['name'].endswith('-slash') else [
                        ('GET', path + '/.well-known/agent-card.json'), ('POST', path)]
                    self.assertEqual(route['matches'], [
                        {'method': method, 'path': {'exact': endpoint}}
                        for method, endpoint in expected])
                    self.assertEqual(route['backends'], [
                        {'host': 'kagent-controller.iot-agents.svc.cluster.local:8083'}])
                    self.assertEqual(route['policies']['apiKey']['keys'], [{'keyHash': hashes['a2a']}])
                    self.assertEqual(route['policies']['requestHeaderModifier'], {
                        'remove': ['authorization', 'x-forwarded-user'],
                        'set': {'x-user-id': 'coursework'}})
                    self.assertEqual(route['policies']['responseHeaderModifier']['set'], {
                        'x-iot-agentgateway-route': 'a2a-ab-retrieval-' + variant})
                    self.assertNotIn('headers', route['matches'][0])
                objects = manifests(hashes, 'agw-ab-route-test', delegation_hash=delegation,
                                    ab_operator_routes=True)
                actual = json.loads(next(o for o in objects if o['kind'] == 'ConfigMap')['data']['config.yaml'])
                self.assertEqual(actual, config(hashes, tracing=True,
                    delegation_hash=delegation, ab_operator_routes=True))
        for value in (1, 'true', [], None):
            with self.subTest(invalid=value), self.assertRaises(ValueError):
                config(hashes, ab_operator_routes=value)


class ABControllerFixture(BackendFixture):
    """Model only the two operator-facing controller endpoints, never real agents."""

    def record(self):
        """Keep safe wire observations to prove rejected calls never reached the backend."""
        self.server.requests.append({'method': self.command, 'path': self.path,
            'user': self.headers.get('X-User-Id'),
            'authorization_present': bool(self.headers.get('Authorization')),
            'forwarded_user': self.headers.get('X-Forwarded-User')})

    def do_GET(self):
        """Require the gateway-selected operator identity for either exact fixture card."""
        self.record()
        for variant, path in AB_PATHS.items():
            if self.path == path + '/.well-known/agent-card.json':
                if self.headers.get('X-User-Id') != 'coursework' or self.headers.get('Authorization'):
                    self.send(403); return
                self.send(200, {'name': 'iot_ab_retrieval_' + variant,
                    'description': 'fixture only', 'version': '0.2.2', 'protocolVersion': '0.3.0',
                    'url': 'http://127.0.0.1:1234' + path + '/', 'capabilities': {},
                    'defaultInputModes': ['text/plain'], 'defaultOutputModes': ['text/plain'], 'skills': []})
                return
        self.send(404)

    def do_POST(self):
        """Check canonical backend paths and return native-shaped fixture tasks."""
        self.record()
        value = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        if (self.path not in AB_PATHS.values() or self.headers.get('X-User-Id') != 'coursework'
                or self.headers.get('Authorization') or self.headers.get('X-Forwarded-User')
                or value.get('method') != 'message/send' or self.headers.get('A2A-Version') != '0.3'):
            self.send(403); return
        self.send(200, {'jsonrpc': '2.0', 'id': value['id'], 'result': {
            'id': 'fixture-ab-task', 'contextId': 'fixture-ab-context', 'status': {'state': 'completed'}}})


class AgentABGatewayBinaryTests(unittest.TestCase):
    """Send A/B cards and tasks through the official binary using synthetic backends."""

    @unittest.skipUnless(os.environ.get('IOT_AGENTGATEWAY_BINARY'), 'set the verified official gateway binary')
    def test_both_variants_route_only_for_operator_credential(self):
        """Wrong-scope credentials fail before dispatch; canonical and slash tasks preserve identity."""
        binary = Path(os.environ['IOT_AGENTGATEWAY_BINARY'])
        self.assertEqual(sha256(binary.read_bytes()).hexdigest(), WINDOWS_DIGEST if os.name == 'nt' else LINUX_DIGEST)
        with TemporaryDirectory() as name, ExitStack() as stack:
            out = Path(name)
            backend = ThreadingHTTPServer(('127.0.0.1', 0), ABControllerFixture)
            backend.requests = []; backend.role = 'a2a'
            Thread(target=backend.serve_forever, daemon=True).start()
            stack.callback(backend.server_close); stack.callback(backend.shutdown)
            ports = {role: unused_port() for role in PORTS}; ready = unused_port()
            value = config({role: bearer_hash(token) for role, token in TOKENS.items()},
                backends={role: f'127.0.0.1:{backend.server_port}' for role in PORTS},
                ports=ports, credential_dir=out.as_posix(), local=True,
                delegation_hash=bearer_hash(DELEGATE_TOKEN), ab_operator_routes=True)
            value['config'].update(adminAddr=f'127.0.0.1:{unused_port()}',
                readinessAddr=f'127.0.0.1:{ready}', statsAddr=f'127.0.0.1:{unused_port()}')
            for role in ('model', 'retrieval', 'drift'):
                (out / role).write_text(TOKENS[role])
            path = out / 'config.json'; path.write_text(json.dumps(value))
            subprocess.run([binary, '--validate-only', '-f', path], check=True, capture_output=True, timeout=15)
            environment = {key: item for key, item in os.environ.items() if key.upper() != 'RUST_LOG'}
            with (out / 'gateway.log').open('wb') as log, subprocess.Popen(
                    [binary, '-f', path], env=environment, stdout=log, stderr=log) as gateway:
                try:
                    deadline = monotonic() + 15
                    while monotonic() < deadline and gateway.poll() is None:
                        connection = HTTPConnection('127.0.0.1', ready, timeout=1)
                        try:
                            connection.request('GET', '/healthz/ready')
                            if connection.getresponse().status == 200: break
                        except OSError: sleep(0.1)
                        finally: connection.close()
                    else: self.fail('Official A/B fixture gateway did not become ready')
                    probe = GatewayProbe('127.0.0.1', TOKENS, out / 'receipts', ports)
                    for variant, endpoint in AB_PATHS.items():
                        for index, token in enumerate((None, DELEGATE_TOKEN, TOKENS['model'],
                                                       TOKENS['retrieval'], TOKENS['drift'])):
                            for suffix, method in [('/.well-known/agent-card.json', 'GET'), ('', 'POST'), ('/', 'POST')]:
                                before = len(backend.requests)
                                headers = {'Authorization': 'Bearer ' + token} if token else {}
                                status, _, _ = probe.request(f'{variant}-denied-{index}-{method}-{len(suffix)}',
                                    'a2a', endpoint + suffix, method=method,
                                    payload=rpc('message/send', 1, {}) if method == 'POST' else None,
                                    headers=headers, auth=False)
                                self.assertIn(status, (401, 403))
                                self.assertEqual(len(backend.requests), before)
                        status, raw, headers = probe.request(variant + '-card', 'a2a',
                            endpoint + '/.well-known/agent-card.json',
                            headers={'x-user-id': 'spoofed-user', 'x-forwarded-user': 'spoofed-forward'})
                        self.assertEqual(status, 200)
                        self.assertEqual(json.loads(raw)['name'], 'iot_ab_retrieval_' + variant)
                        self.assertEqual(headers['x-iot-agentgateway-route'], 'a2a-ab-retrieval-' + variant)
                        for label, suffix in [('canonical', ''), ('slash', '/')]:
                            status, raw, headers = probe.request(variant + '-' + label, 'a2a', endpoint + suffix,
                                method='POST', payload=rpc('message/send', 11, {}),
                                headers={'A2A-Version': '0.3', 'x-user-id': 'spoofed-user',
                                         'x-forwarded-user': 'spoofed-forward'})
                            self.assertEqual(status, 200)
                            self.assertEqual(json.loads(raw)['result']['status']['state'], 'completed')
                            self.assertEqual(headers['x-iot-agentgateway-route'], 'a2a-ab-retrieval-' + variant)
                            self.assertEqual(backend.requests[-1]['path'], endpoint)
                        before = len(backend.requests)
                        for suffix, method in [('/extra', 'POST'), ('', 'GET')]:
                            status, _, _ = probe.request(variant + '-unexpected-' + method, 'a2a', endpoint + suffix,
                                method=method, payload=rpc('message/send', 12, {}) if method == 'POST' else None)
                            self.assertEqual(status, 404)
                        self.assertEqual(len(backend.requests), before)
                    self.assertEqual(len(backend.requests), 6)
                    for observed in backend.requests:
                        self.assertEqual(observed['user'], 'coursework')
                        self.assertFalse(observed['authorization_present'])
                        self.assertIsNone(observed['forwarded_user'])
                finally:
                    gateway.terminate(); gateway.wait(timeout=10)
            access = (out / 'gateway.log').read_text(encoding='utf-8')
            for token in [*TOKENS.values(), DELEGATE_TOKEN]:
                self.assertNotIn(token, access)


if __name__ == '__main__':
    unittest.main()
