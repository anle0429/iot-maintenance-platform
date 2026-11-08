"""Require mediated child discovery/RPCs and preserve bounded role-specific MCP routes."""
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

import yaml
from infra.agentgateway.candidate import DNS_NAME, PORTS, a2a_path, bearer_hash, config, manifests
from infra.agentgateway.probe import GatewayProbe, rpc
from tests.test_agentgateway_candidate import BackendFixture, TOKENS, WINDOWS_DIGEST, LINUX_DIGEST, unused_port

ROOT = Path(__file__).resolve().parents[1]
DELEGATE_TOKEN = 'delegation-fixture-' + 'c' * 32
DELEGATE_USER = 'fixture-delegated-user'
DELEGATE_HEADERS = [{'name': 'Authorization', 'valueFrom': {
    'type': 'Secret', 'name': 'iot-agentgateway-delegation-client', 'key': 'Authorization'}}]


class DelegationGatewayContractTests(unittest.TestCase):
    """Check the actual chart, supported controller settings and scoped fixed-backend routes."""

    def test_controller_mediates_discovery_and_advertised_agent_interfaces(self):
        """Both compiler proxying and card advertisement are needed in pinned kagent0.10.3."""
        overlay = yaml.safe_load((ROOT/'infra/agentgateway/kagent-default.yaml').read_text())
        endpoint = f'http://{DNS_NAME}:18083'
        self.assertEqual(overlay.get('proxy', {}).get('url'), endpoint)
        self.assertEqual(overlay.get('controller', {}).get('a2aBaseUrl'), endpoint)

    def test_gateway_chart_has_secret_backed_delegation_and_distinct_mcp_paths(self):
        """Native compiler proxying keeps the role path even when it drops the original port."""
        raw = subprocess.check_output(['helm', 'template', 'iot-agents', str(ROOT/'infra/charts/iot-agents'),
                                     '-n', 'iot-agents', '--set', 'agentgateway.enabled=true'])
        documents = list(yaml.safe_load_all(raw))
        parent = next(d for d in documents if d and d['kind'] == 'SandboxAgent'
                      and d['metadata']['name'] == 'iot-coordinator')
        for tool in parent['spec']['declarative']['tools']:
            self.assertTrue(tool['isolateSessions'])
            self.assertEqual(tool.get('headersFrom'), DELEGATE_HEADERS)
        for role in ('retrieval', 'drift'):
            server = next(d for d in documents if d and d['kind'] == 'RemoteMCPServer'
                          and d['metadata']['name'] == f'iot-gateway-{role}-tools')
            self.assertEqual(server['spec']['url'], f'http://{DNS_NAME}:18083/mcp/{role}')

    def test_delegation_credential_is_distinct_and_only_accepts_specialist_routes(self):
        """A coordinator token must not call itself or gain model/MCP credential privileges."""
        self.assertIn('delegation_hash', inspect.signature(config).parameters)
        hashes = {role: bearer_hash(token) for role, token in TOKENS.items()}
        delegate = bearer_hash(DELEGATE_TOKEN)
        value = config(hashes, delegation_hash=delegate)
        routes = next(b['listeners'][0]['routes'] for b in value['binds'] if b['port'] == PORTS['a2a'])
        delegated = [r for r in routes if r['name'].startswith('delegate-')]
        self.assertEqual({r['name'] for r in delegated}, {
            'delegate-retrieval', 'delegate-retrieval-slash', 'delegate-drift', 'delegate-drift-slash'})
        for route in delegated:
            self.assertEqual(route['policies']['apiKey']['keys'], [{'keyHash': delegate}])
            self.assertEqual(route['backends'], [{'host': 'kagent-controller.iot-agents.svc.cluster.local:8083'}])
            self.assertIn('x-kagent-host', route['policies']['requestHeaderModifier']['remove'])
        for role in ('retrieval', 'drift'):
            alias = next(r for r in routes if r['name'] == 'proxy-mcp-' + role)
            self.assertEqual({m['path']['exact'] for m in alias['matches']}, {'/mcp/' + role})
            self.assertEqual(alias['policies']['apiKey']['keys'], [{'keyHash': hashes[role]}])
        for digest in ['bad', hashes['a2a'], hashes['model']]:
            with self.assertRaises(ValueError):
                config(hashes, delegation_hash=digest)
        objects = manifests(hashes, 'agw-delegation-test', delegation_hash=delegate)
        policy = next(o['spec'] for o in objects if o['kind'] == 'NetworkPolicy')
        peers = next(r['from'] for r in policy['ingress'] if r['ports'][0]['port'] == PORTS['a2a'])
        worker_pools = {p['podSelector']['matchLabels'].get('ate.dev/worker-pool') for p in peers}
        self.assertTrue({'iot-retrieval-pool', 'iot-drift-pool', 'iot-coordinator-pool'} <= worker_pools)
        self.assertNotIn(DELEGATE_TOKEN, json.dumps(objects))


class RecordingController(BackendFixture):
    """Retain only fixture lineage headers and paths, never credentials or payload content."""

    def record_request(self):
        """Count every backend request and retain only non-secret fixture attribution."""
        observed = {key: self.headers.get(key) for key in
            ['x-user-id', 'x-kagent-parent-context-id', 'x-kagent-root-context-id',
             'traceparent', 'tracestate', 'x-kagent-host']}
        observed.update(method=self.command, path=self.path)
        self.server.requests.append(observed)
        if self.server.role == 'a2a' and self.command == 'POST':
            self.server.lineage.append(observed)

    def do_GET(self):
        """Accept specialist fixture cards under the delegated or fixed operator identity."""
        self.record_request()
        if self.server.role == 'a2a':
            for role in ('retrieval', 'drift'):
                if self.path == a2a_path(role) + '/.well-known/agent-card.json':
                    if (self.headers.get('X-User-Id') not in {DELEGATE_USER, 'coursework'}
                            or self.headers.get('Authorization')):
                        self.send(403); return
                    self.send(200, {'name': 'iot_' + role, 'description': 'fixture only', 'version': '0.0.0',
                        'protocolVersion': '0.3.0', 'url': 'http://127.0.0.1:1234/',
                        'capabilities': {}, 'defaultInputModes': ['text/plain'],
                        'defaultOutputModes': ['text/plain'], 'skills': []})
                    return
        super().do_GET()

    def do_POST(self):
        """Observe attribution before applying the existing synthetic controller contract."""
        self.record_request()
        if self.server.role == 'a2a':
            if self.path in {a2a_path('retrieval'), a2a_path('drift')}:
                value = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                if (self.headers.get('X-User-Id') != DELEGATE_USER or self.headers.get('Authorization')
                        or self.headers.get('A2A-Version') != '0.3' or value['method'] != 'message/send'):
                    self.send(403); return
                self.send(200, {'jsonrpc': '2.0', 'id': value['id'], 'result': {
                    'id': 'fixture-child', 'contextId': 'fixture-child-context', 'status': {'state': 'completed'}}})
                return
        super().do_POST()


class DelegationGatewayBinaryTests(unittest.TestCase):
    """Exercise the official proxy with loopback fixtures; this is not live-agent evidence."""

    @unittest.skipUnless(os.environ.get('IOT_AGENTGATEWAY_BINARY'), 'set the verified official gateway binary')
    def test_mcp_aliases_and_child_calls_pass_while_cross_scope_calls_fail(self):
        """Actual HTTP traverses the pinned gateway with MCP handshake and A2A context propagation."""
        binary = Path(os.environ['IOT_AGENTGATEWAY_BINARY'])
        self.assertEqual(sha256(binary.read_bytes()).hexdigest(), WINDOWS_DIGEST if os.name == 'nt' else LINUX_DIGEST)
        with TemporaryDirectory() as name, ExitStack() as stack:
            out = Path(name); backends = {}; servers = {}
            for role in PORTS:
                backend = ThreadingHTTPServer(('127.0.0.1', 0), RecordingController)
                backend.role = role; backend.lineage = []; backend.requests = []; servers[role] = backend
                Thread(target=backend.serve_forever, daemon=True).start()
                stack.callback(backend.server_close); stack.callback(backend.shutdown)
                backends[role] = f'127.0.0.1:{backend.server_port}'
                (out/role).write_text(TOKENS[role])
            ports = {role: unused_port() for role in PORTS}; ready = unused_port()
            value = config({r: bearer_hash(t) for r, t in TOKENS.items()}, backends=backends,
                ports=ports, credential_dir=out.as_posix(), local=True, delegation_hash=bearer_hash(DELEGATE_TOKEN))
            value['config'].update(adminAddr=f'127.0.0.1:{unused_port()}',
                                   readinessAddr=f'127.0.0.1:{ready}', statsAddr=f'127.0.0.1:{unused_port()}')
            path = out/'config.json'; path.write_text(json.dumps(value))
            subprocess.run([binary, '--validate-only', '-f', path], check=True, capture_output=True, timeout=15)
            environment = {key:value for key,value in os.environ.items() if key.upper() != 'RUST_LOG'}
            with (out/'gateway.log').open('wb') as log, subprocess.Popen([binary, '-f', path],
                    env=environment, stdout=log, stderr=log) as gateway:
                try:
                    deadline = monotonic()+15
                    while monotonic() < deadline and gateway.poll() is None:
                        connection = HTTPConnection('127.0.0.1', ready, timeout=1)
                        try:
                            connection.request('GET', '/healthz/ready')
                            if connection.getresponse().status == 200: break
                        except OSError: sleep(0.1)
                        finally: connection.close()
                    else: self.fail('Fixture gateway did not become ready')
                    probe = GatewayProbe('127.0.0.1', TOKENS, out/'receipts',
                        {**ports, 'retrieval': ports['a2a'], 'drift': ports['a2a']})
                    for role in ('retrieval', 'drift'):
                        probe.mcp(role, {'fixture': True}, path='/mcp/'+role, route='proxy-mcp-'+role)
                    context = {'x-kagent-parent-context-id': '11111111-1111-4111-8111-111111111111',
                        'x-kagent-root-context-id': '33333333-3333-4333-8333-333333333333',
                        'x-kagent-source': 'agent', 'x-kagent-host': 'kagent-controller.iot-agents',
                        'x-user-id': DELEGATE_USER, 'A2A-Version': '0.3',
                        'traceparent': '00-'+'1'*32+'-'+'2'*16+'-01', 'tracestate': 'fixture=delegated-state',
                        'Authorization': 'Bearer '+DELEGATE_TOKEN}
                    for role in ('retrieval', 'drift'):
                        endpoint = a2a_path(role)
                        for suffix in ('', '/'):
                            label = role+'-delegate-'+('slash' if suffix else 'canonical')
                            status, _, headers = probe.request(label, 'a2a', endpoint+suffix,
                                method='POST', payload=rpc('message/send', 7, {}), headers=context, auth=False)
                            self.assertEqual(status, 200)
                            self.assertEqual(headers['x-iot-agentgateway-route'], 'delegate-'+role)
                        status, _, headers = probe.request(role+'-card', 'a2a', endpoint+'/.well-known/agent-card.json', headers=context, auth=False)
                        self.assertEqual(status, 200)
                        self.assertEqual(headers['x-iot-agentgateway-route'], 'delegate-'+role)
                        self.assertEqual(servers['a2a'].requests[-1]['x-user-id'], DELEGATE_USER)

                    def reject_without_backend(label, endpoint, headers, *, method='POST'):
                        """Require gateway rejection before any loopback backend receives a request."""
                        before = {role: len(server.requests) for role, server in servers.items()}
                        status, _, _ = probe.request('deny-'+label, 'a2a', endpoint, method=method,
                            payload=rpc('message/send', 8, {}) if method == 'POST' else None,
                            headers=headers, auth=False)
                        self.assertIn(status, (401, 403, 404))
                        self.assertEqual({role: len(server.requests) for role, server in servers.items()}, before)

                    for label, endpoint in [('self', a2a_path('coordinator')), ('wrong-protocol', '/v1/chat/completions'), ('mcp', '/mcp/retrieval')]:
                        reject_without_backend(label, endpoint, context)
                    for role in ('retrieval', 'drift'):
                        for header_case in ('missing', 'wrong'):
                            changed = deepcopy(context)
                            changed.pop('x-kagent-host')
                            if header_case == 'wrong':
                                changed['x-kagent-host'] = 'kagent-controller.iot-agents.svc.cluster.local'
                            for suffix in ('', '/'):
                                reject_without_backend(role+'-'+header_case+'-host-'+str(len(suffix)),
                                    a2a_path(role)+suffix, changed)
                            reject_without_backend(role+'-'+header_case+'-host-card',
                                a2a_path(role)+'/.well-known/agent-card.json', changed, method='GET')
                        for credential_role in TOKENS:
                            changed = {**context, 'Authorization': 'Bearer '+TOKENS[credential_role]}
                            reject_without_backend(role+'-wrong-scope-'+credential_role, a2a_path(role), changed)
                    self.assertEqual(len(servers['a2a'].lineage), 4)
                    for observed in servers['a2a'].lineage:
                        for key in ['x-user-id', 'x-kagent-parent-context-id', 'x-kagent-root-context-id']:
                            self.assertEqual(observed[key], context[key])
                        # The real gateway adds a transport span: keep the trace
                        # identity while forwarding its new downstream span ID.
                        self.assertEqual(observed['traceparent'].split('-')[1], context['traceparent'].split('-')[1])
                        self.assertRegex(observed['traceparent'], r'^00-[0-9a-f]{32}-[0-9a-f]{16}-01$')
                        self.assertEqual(observed['tracestate'], context['tracestate'])
                        self.assertIsNone(observed['x-kagent-host'])
                    for role in ('retrieval', 'drift'):
                        status, _, headers = probe.request(role+'-operator-card', 'a2a',
                            a2a_path(role)+'/.well-known/agent-card.json',
                            headers={'x-user-id': 'ignored-operator-spoof'})
                        self.assertEqual(status, 200)
                        self.assertEqual(headers['x-iot-agentgateway-route'], 'a2a-'+role)
                        self.assertEqual(servers['a2a'].requests[-1]['x-user-id'], 'coursework')
                    probe.a2a('coordinator', 'Fixture operator protocol check only.')
                    self.assertEqual(servers['a2a'].requests[-2]['method'], 'GET')
                    self.assertEqual(servers['a2a'].requests[-2]['x-user-id'], 'coursework')
                    self.assertEqual(servers['a2a'].lineage[-1]['path'], a2a_path('coordinator'))
                    self.assertEqual(servers['a2a'].lineage[-1]['x-user-id'], 'coursework')
                finally:
                    gateway.terminate(); gateway.wait(timeout=10)
            access = (out/'gateway.log').read_text(encoding='utf-8')
            records = []
            for line in access.splitlines():
                try: records.append(json.loads(line))
                except ValueError: continue
            joined = [r for r in records if r.get('http.status') == 200
                      and r.get('a2a_parent_context_id') == context['x-kagent-parent-context-id']]
            self.assertTrue({'delegate-retrieval', 'delegate-drift'} <= {
                r.get('route', '').rsplit('/', 1)[-1] for r in joined},
                [(r.get('route'),r.get('http.status'),r.get('a2a_parent_context_id'),
                  r.get('a2a_root_context_id'),r.get('a2a_source')) for r in records if r.get('route')])
            self.assertTrue(all(r.get('a2a_root_context_id') == context['x-kagent-root-context-id']
                                and r.get('a2a_source') == 'agent' for r in joined))
            for token in [*TOKENS.values(), DELEGATE_TOKEN]: self.assertNotIn(token, access)


if __name__ == '__main__': unittest.main()
