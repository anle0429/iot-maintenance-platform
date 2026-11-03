"""Probe authenticated model, role-specific MCP and A2A routes without recording credentials."""

from hashlib import sha256
from http.client import HTTPConnection
from pathlib import Path
from time import monotonic
from uuid import uuid4
import json
import re

from infra.agentgateway.candidate import PORTS, a2a_path


MAX_RESPONSE = 2 * 1024 * 1024
TOOLS = {'retrieval': {'get_machine_features', 'predict_failure', 'search_knowledge'}, 'drift': {'assess_feature_drift'}}


def rpc(method, request_id, params=None):
    """Form a JSON-RPC request or notification with the caller's native method name."""
    result = {'jsonrpc': '2.0', 'method': method}
    if request_id is not None:
        result['id'] = request_id
    if params is not None:
        result['params'] = params
    return result


class GatewayProbe:
    """Use an explicit TCP destination; never follow a redirect with a credential."""

    def __init__(self, host, tokens, output, ports=PORTS):
        """Retain a new output directory and per-port credentials in memory only."""
        self.host = host
        self.tokens = tokens
        self.ports = ports
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.receipts = {}

    def request(self, label, role, path, *, method='GET', payload=None, headers=None, auth=True, timeout=45):
        """Record native body and safe transport facts, rejecting oversized or credential-bearing responses."""
        if label in self.receipts:
            raise ValueError('Preserve the prior request receipt')
        supplied = dict(headers or {})
        if auth:
            supplied['Authorization'] = 'Bearer ' + self.tokens[role]
        body = json.dumps(payload).encode() if payload is not None else None
        if body is not None:
            supplied['Content-Type'] = 'application/json'
        conn = HTTPConnection(self.host, self.ports[role], timeout=timeout)
        started = monotonic()
        try:
            conn.request(method, path, body, supplied)
            result = conn.getresponse()
            raw = result.read(MAX_RESPONSE + 1)
            result_headers = dict((key.lower(), value) for key, value in result.getheaders())
        finally:
            conn.close()
        if len(raw) > MAX_RESPONSE or any(token.encode() in raw for token in self.tokens.values()):
            raise ValueError('Response exceeded its bound or exposed a credential; no body saved')
        item = {'role': role, 'method': method, 'path': path, 'http_status': result.status,
                'gateway_route': result_headers.get('x-iot-agentgateway-route'),
                'elapsed_seconds': monotonic() - started, 'body_sha256': sha256(raw).hexdigest()}
        observer_id = result_headers.get('x-iot-request-id', '')
        if re.fullmatch(r'[0-9a-f]{32}', observer_id):
            item['observer_request_id'] = observer_id
        self.receipts[label] = item
        (self.output / (label + '.body')).write_bytes(raw)
        (self.output / 'requests.json').write_text(json.dumps(self.receipts, indent=2) + '\n')
        return result.status, raw, result_headers

    def reject(self, label, role, path, token_role=None, *, method='GET', payload=None):
        """Prove missing or wrong-role credentials fail before a successful backend response."""
        headers = {'Authorization': 'Bearer ' + self.tokens[token_role]} if token_role else {}
        status, _, _ = self.request(label, role, path, method=method, payload=payload, headers=headers, auth=False)
        if status not in (401, 403):
            raise ValueError(f'Gateway did not reject missing or cross-role credentials: HTTP {status}')

    def model(self, model='iot-qwen2.5-7b'):
        """Require a real routed completion body with model alias, text and coherent token usage."""
        path = '/v1/chat/completions'
        payload = {'model': model, 'messages': [{'role': 'user', 'content': 'Name one use of a vibration sensor.'}],
                   'temperature': 0, 'seed': 7, 'max_tokens': 32, 'stream': False}
        self.reject('model-unauthenticated', 'model', path, method='POST', payload=payload)
        status, body, headers = self.request('model-completion', 'model', path, method='POST', payload=payload)
        reply = json.loads(body)
        usage = reply['usage']
        if (status != 200 or headers.get('x-iot-agentgateway-route') != 'model' or reply.get('model') != model
                or not reply['choices'][0]['message']['content'].strip() or usage['completion_tokens'] <= 0
                or usage['prompt_tokens'] + usage['completion_tokens'] != usage['total_tokens']):
            raise ValueError('Expected native model generation through the candidate route')
        return reply

    def mcp(self, role, arguments):
        """Check initialize, role-only tools/list and one real tools/call response per specialist route."""
        if role not in TOOLS:
            raise ValueError('Unknown MCP role')
        headers = {'Accept': 'application/json, text/event-stream'}
        payload = rpc('initialize', 1, {'protocolVersion': '2025-03-26', 'capabilities': {},
                                       'clientInfo': {'name': 'iot-agentgateway-probe', 'version': '1.0'}})
        wrong = 'drift' if role == 'retrieval' else 'retrieval'
        self.reject(role + '-cross-role', role, '/mcp', wrong, method='POST', payload=payload)
        status, raw, received = self.request(role + '-initialize', role, '/mcp', method='POST', payload=payload, headers=headers)
        initialized = mcp_result(status, raw, received, role, 1)
        if 'mcp-session-id' in received:
            headers['Mcp-Session-Id'] = received['mcp-session-id']
        headers['MCP-Protocol-Version'] = initialized['protocolVersion']
        status, _, _ = self.request(role + '-ack', role, '/mcp', method='POST', headers=headers,
                                    payload=rpc('notifications/initialized', None))
        if status not in (200, 202, 204):
            raise ValueError('MCP initialize notification rejected')
        status, raw, received = self.request(role + '-list', role, '/mcp', method='POST', headers=headers,
                                             payload=rpc('tools/list', 2, {}))
        names = {item['name'] for item in mcp_result(status, raw, received, role, 2)['tools']}
        if names != TOOLS[role]:
            raise ValueError('Gateway changed tool names or exposed another role')
        name = 'get_machine_features' if role == 'retrieval' else 'assess_feature_drift'
        status, raw, received = self.request(role + '-tool', role, '/mcp', method='POST', headers=headers,
            payload=rpc('tools/call', 3, {'name': name, 'arguments': {'request': arguments}}))
        value = mcp_result(status, raw, received, role, 3)
        if value.get('isError') or not (value.get('content') or value.get('structuredContent')):
            raise ValueError('Expected a successful native MCP business-tool result')
        return value

    def a2a(self, role, question=None):
        """Check an A2A v0.3 card and, when requested, send a fresh blocking native task."""
        path = a2a_path(role)
        headers = {'Accept': 'application/json', 'A2A-Version': '0.3', 'X-User-Id': 'ignored-spoof-check'}
        self.reject(role + '-a2a-unauthenticated', 'a2a', path + '/.well-known/agent-card.json')
        status, raw, received = self.request(role + '-a2a-card', 'a2a', path + '/.well-known/agent-card.json', headers=headers)
        card = json.loads(raw)
        expected_url = f'http://{self.host}:{self.ports["a2a"]}{path}'
        interfaces = card.get('supportedInterfaces', [])
        advertised = [item.get('url', '').rstrip('/') for item in interfaces
                      if item.get('protocolVersion') == '0.3' and item.get('protocolBinding') == 'JSONRPC']
        if not interfaces:
            advertised = [card.get('url', '').rstrip('/')]
        if (status != 200 or received.get('x-iot-agentgateway-route') != 'a2a-' + role
                or card.get('name') != 'iot_' + role or advertised != [expected_url]):
            raise ValueError('Expected the actual named agent card through the candidate route')
        if question is None:
            return card
        request_id = str(uuid4())
        body = rpc('message/send', request_id, {'message': {'role': 'user', 'messageId': str(uuid4()),
            'contextId': str(uuid4()), 'parts': [{'kind': 'text', 'text': question}]}, 'configuration': {'blocking': True}})
        (self.output / (role + '-a2a-request.json')).write_text(json.dumps(body, indent=2) + '\n')
        status, raw, received = self.request(role + '-a2a-task', 'a2a', path, method='POST', payload=body,
                                             headers=headers, timeout=1800)
        reply = json.loads(raw)
        if (status != 200 or received.get('x-iot-agentgateway-route') != 'a2a-' + role
                or reply.get('id') != request_id or reply.get('error')
                or reply.get('result', {}).get('status', {}).get('state') != 'completed'):
            raise ValueError('The native A2A task did not complete through the candidate')
        return reply


def mcp_result(status, raw, headers, role, request_id):
    """Decode a native MCP JSON or SSE response and require the matching successful RPC ID."""
    if status != 200 or headers.get('x-iot-agentgateway-route') != role:
        raise ValueError('MCP response did not come through the intended authenticated route')
    if headers.get('content-type', '').startswith('text/event-stream'):
        messages = [json.loads(line[5:].strip()) for line in raw.decode().splitlines() if line.startswith('data:')]
        value = next(message for message in messages if message.get('id') == request_id)
    else:
        value = json.loads(raw)
    if value.get('id') != request_id or 'error' in value:
        raise ValueError('The MCP JSON-RPC response did not succeed')
    return value['result']


def checked_tool_facts(role, result, *, window=None):
    """Reject empty, misidentified or unbound machine and drift results from a native tool call."""
    facts = result.get('structuredContent')
    if facts is None:
        facts = json.loads(result['content'][0]['text'])
    if len(facts.get('features', {})) != 8 or not facts.get('as_of'):
        raise ValueError('Expected eight feature identities and actual snapshot time')
    if not re.fullmatch(r'[0-9a-f]{64}', facts.get('source_manifest_sha256', '')):
        raise ValueError('Tool did not return its native source identity')
    if role == 'retrieval' and facts.get('machine_id') == 'P-001' and facts.get('feature_event_ts'):
        return facts
    if (role == 'drift' and facts.get('window_id') == window and facts.get('unique_rows') == 32
            and facts.get('reference_id') and isinstance(facts.get('drift_detected'), bool)
            and re.fullmatch(r'[0-9a-f]{64}', facts.get('assessment_sha256', ''))):
        return facts
    raise ValueError('Native tool identities or cohort do not match this probe')
