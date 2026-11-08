"""Reject falsely attributed or selectively edited A/B gateway evidence using explicit synthetic wire fixtures."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib
import importlib.util
import json
from pathlib import Path
import tarfile
import unittest
from uuid import uuid4

from agents.answer_acceptance import native_tools, verify_specialist
from agents.ab_configuration import VARIANTS
from agents.probe import QUESTIONS

ROOT = Path(__file__).resolve().parents[1]


def encode(value):
    """Keep finite, unambiguous JSON bytes for the synthetic receipt mutations."""
    return (json.dumps(value, allow_nan=False) + '\n').encode()


def fixture():
    """Reuse real tool contents but explicitly replace all run, actor and wire identities with synthetic fixtures."""
    files = {}; start = datetime(2026, 11, 8, 9, tzinfo=timezone.utc)
    portable = (ROOT / 'docs/evidence/ci-serving-linux/selected-model6/portable.json').read_bytes()
    pin = json.loads((ROOT / 'docs/evidence/ci-serving-linux/selected-model6/model-pin.json').read_bytes())
    with tarfile.open(ROOT / 'docs/evidence/llm-agent-ab/native-receipts.tar.gz') as archive:
        names = archive.getnames()
        for index, (variant, machine) in enumerate([
                ('a', 'P-001'), ('b', 'P-001'), ('b', 'P-002'),
                ('a', 'P-002'), ('a', 'P-003'), ('b', 'P-003')]):
            case = index // 2; label = f'{case}-{variant}'; context = str(uuid4())
            now = start + timedelta(seconds=index * 30); finish = now + timedelta(seconds=20)
            source = next(n for n in names if n.endswith(f'agent-ab-study-30b/{label}/response.json'))
            reply = json.load(archive.extractfile(source)); request_id = str(uuid4())
            reply['id'] = request_id; task = reply['result']; task['contextId'] = context
            task['status']['timestamp'] = (finish - timedelta(seconds=1)).isoformat()
            for message in task['history']:
                message['contextId'] = context
                for part in message.get('parts', []):
                    if part.get('metadata', {}).get('adk_type') != 'function_response': continue
                    data = part['data']; response = data.get('response', {})
                    if 'output' not in response: continue
                    value = response['output']
                    if isinstance(value, str): value = json.loads(value)
                    if data['name'] == 'get_machine_features':
                        value['prepared_wall_utc'] = (now - timedelta(seconds=10)).isoformat()
                    if data['name'] == 'predict_failure':
                        value['feature_trace']['prepared_wall_utc'] = (now - timedelta(seconds=10)).isoformat()
                    response['output'] = json.dumps(value)
            payload = {'jsonrpc': '2.0', 'id': request_id, 'method': 'message/send', 'params': {
                'message': {'role': 'user', 'messageId': str(uuid4()), 'contextId': context,
                    'parts': [{'kind': 'text', 'text': QUESTIONS['retrieval'].replace('P-001', machine)}]},
                'configuration': {'blocking': True}}}
            outcome = {'variant': variant, 'machine_id': machine, 'context_id': context,
                'request_id': request_id, 'http_status': 200, 'response_complete': True, 'passed': True,
                'elapsed_seconds': 20., 'started_utc': now.isoformat(), 'finished_utc': finish.isoformat(),
                'task_id': task['id'], 'successful_tool_events': 3, 'tool_error_events': 0}
            agent = VARIANTS[variant]['agent']; actor_name = 'asr-' + sha256(
                ('iot-agents/' + agent + '/' + context).encode()).hexdigest()[:24]
            pod_name = 'fixture-retrieval-worker'; pod_uid = 'fixture-worker-uid'; ip = '10.52.0.42'
            actor = {'metadata': {'atespace': 'iot-agents', 'name': actor_name, 'uid': 'fixture-actor-' + context},
                'status': 'STATUS_RUNNING', 'actorTemplateNamespace': 'iot-agents',
                'actorTemplateName': agent + '-0123456789abcdef', 'ateomPodNamespace': 'iot-agents',
                'ateomPodName': pod_name, 'ateomPodUid': pod_uid, 'ateomPodIp': ip,
                'workerPoolName': 'iot-retrieval-pool'}
            pods = {'apiVersion': 'v1', 'kind': 'PodList', 'items': [{'apiVersion': 'v1', 'kind': 'Pod',
                'metadata': {'namespace': 'iot-agents', 'name': pod_name, 'uid': pod_uid,
                    'labels': {'ate.dev/worker-pool': 'iot-retrieval-pool'}},
                'status': {'phase': 'Running', 'podIP': ip, 'conditions': [{'type': 'Ready', 'status': 'True'}]}}]}
            path = '/api/a2a-sandboxes/iot-agents/' + agent
            rows = [{'time': now.isoformat(), 'route': 'default/a2a-ab-retrieval-' + variant,
                'src.addr': '10.42.0.2:40000', 'http.method': 'POST', 'http.path': path, 'http.status': 200,
                'a2a.method': 'message/send', 'a2a.response.outcome': 'success',
                'a2a.result.kind': 'task', 'a2a.task.state': 'completed', 'a2a.context.id': context}]
            for i, tool in enumerate(['get_machine_features', 'predict_failure', 'search_knowledge']):
                rows.append({'time': (now + timedelta(seconds=i + 3)).isoformat(),
                    'route': 'default/proxy-mcp-retrieval', 'src.addr': ip + ':41000',
                    'http.method': 'POST', 'http.path': '/mcp/retrieval', 'http.status': 200,
                    'protocol': 'mcp', 'mcp.method.name': 'tools/call', 'mcp.target': 'retrieval',
                    'gen_ai.tool.name': tool, 'mcp.session.id': 'fixture-session-' + context})
            rows.append({'time': (now + timedelta(seconds=8)).isoformat(), 'route': 'default/model',
                'src.addr': ip + ':42000', 'http.method': 'POST', 'http.path': '/v1/chat/completions',
                'http.status': 200, 'protocol': 'http', 'observer_request_id': sha256(context.encode()).hexdigest()[:32]})
            for name, value in [('request', payload), ('response', reply), ('outcome', outcome), ('actor', actor),
                    ('worker-pods', pods), ('answer-acceptance', verify_specialist(reply, 'retrieval', machine_id=machine))]:
                files[label + '/' + name + '.json'] = encode(value)
            files[label + '/gateway-before.log'] = b''
            files[label + '/gateway-after.log'] = b''.join(encode(row) for row in rows)
    return files, portable, pin


class ABGatewayAcceptanceTests(unittest.TestCase):
    """Native content, actor assignment and actual wire attribution must all agree for every pair."""

    def check(self, files, portable, pin):
        """Fail clearly while the new independent verifier API is absent."""
        self.assertIsNotNone(importlib.util.find_spec('agents.ab_gateway_acceptance'))
        module = importlib.import_module('agents.ab_gateway_acceptance')
        return module.verify_ab_comparison(files, portable_bytes=portable, model_pin=pin)

    def test_complete_synthetic_cohort_checks_actual_tools_and_arithmetic(self):
        """Six independently attributed fixtures pass without becoming native deployment evidence."""
        files, portable, pin = fixture(); result = self.check(files, portable, pin)
        self.assertTrue(result['passed'])
        self.assertEqual(result['requests'], 6)
        self.assertEqual(result['successful_mcp_events'], 18)
        self.assertEqual(result['model_requests'], 6)
        self.assertEqual(result['independent_prediction_max_error'], 0.)
        self.assertEqual(set(result['summary']), {'a', 'b'})

    def test_actor_worker_context_and_variant_mutations_fail(self):
        """Changing any ownership join prevents another actor's successful traffic from proving this request."""
        files, portable, pin = fixture()
        mutations = [('actor.json', ['metadata', 'name'], 'asr-wrong-context'),
            ('actor.json', ['actorTemplateName'], 'iot-ab-retrieval-b-0123456789abcdef'),
            ('actor.json', ['ateomPodUid'], 'different-uid'), ('actor.json', ['ateomPodIp'], '10.52.0.99'),
            ('actor.json', ['status'], 'STATUS_SUSPENDED'),
            ('worker-pods.json', ['items', 0, 'metadata', 'labels', 'ate.dev/worker-pool'], 'iot-drift-pool'),
            ('outcome.json', ['context_id'], str(uuid4())), ('outcome.json', ['variant'], 'b')]
        for name, keys, replacement in mutations:
            changed = dict(files); value = json.loads(changed['0-a/' + name]); cursor = value
            for key in keys[:-1]: cursor = cursor[key]
            cursor[keys[-1]] = replacement; changed['0-a/' + name] = encode(value)
            with self.subTest(name=name, keys=keys), self.assertRaises(ValueError):
                self.check(changed, portable, pin)

    def test_gateway_context_paths_errors_and_hidden_log_edits_fail(self):
        """A valid final answer cannot cover a failed, misrouted, missing or incorrectly attributed wire request."""
        files, portable, pin = fixture()
        mutations = [(0, 'http.path', '/api/a2a-sandboxes/iot-agents/iot-ab-retrieval-b'),
            (0, 'a2a.context.id', str(uuid4())), (0, 'a2a.task.state', 'failed'),
            (0, 'route', 'default/a2a-retrieval'), (1, 'src.addr', '10.52.0.99:41000'),
            (1, 'gen_ai.tool.name', 'start_pump'), (1, 'http.status', 503),
            (4, 'observer_request_id', 'missing-id'), (4, 'http.path', '/v1/models')]
        for index, key, replacement in mutations:
            changed = dict(files); rows = [json.loads(line) for line in files['0-a/gateway-after.log'].splitlines()]
            rows[index][key] = replacement; changed['0-a/gateway-after.log'] = b''.join(encode(row) for row in rows)
            with self.subTest(index=index, key=key), self.assertRaises(ValueError):
                self.check(changed, portable, pin)
        for before, after in [(b'previous-record\n', files['0-a/gateway-after.log']),
                (b'', files['0-a/gateway-after.log'] + b'{malformed\n'),
                (b'', b'filtered successful lines only\n' + files['0-a/gateway-after.log'])]:
            changed = dict(files); changed['0-a/gateway-before.log'] = before; changed['0-a/gateway-after.log'] = after
            with self.subTest(before=before), self.assertRaises(ValueError): self.check(changed, portable, pin)

    def test_failed_task_wrong_prediction_or_fabricated_clocks_fail(self):
        """Original task completion, independent pinned arithmetic and finite sequential clocks are required."""
        files, portable, pin = fixture()
        for mode in ['task-failed', 'wrong-prediction', 'future-finished', 'overlap', 'missing-case']:
            changed = dict(files)
            if mode == 'missing-case': del changed['2-b/response.json']
            elif mode in ('future-finished', 'overlap'):
                key = '0-a/outcome.json' if mode == 'future-finished' else '0-b/outcome.json'
                value = json.loads(changed[key])
                value['finished_utc' if mode == 'future-finished' else 'started_utc'] = '2026-11-08T08:59:00+00:00'
                changed[key] = encode(value)
            else:
                value = json.loads(changed['0-a/response.json'])
                if mode == 'task-failed': value['result']['status']['state'] = 'failed'
                else:
                    for message in value['result']['history']:
                        for part in message.get('parts', []):
                            if part.get('metadata', {}).get('adk_type') == 'function_response' and part['data']['name'] == 'predict_failure':
                                payload = json.loads(part['data']['response']['output'])
                                payload['prediction']['risk_score'] += 0.1
                                part['data']['response']['output'] = json.dumps(payload)
                    answer = json.loads(value['result']['artifacts'][0]['parts'][0]['text'])
                    answer['risk_score'] += 0.1
                    value['result']['artifacts'][0]['parts'][0]['text'] = json.dumps(answer)
                    # A coherent invented score still passes answer-vs-tool
                    # comparison; only the independent model oracle rejects it.
                    verify_specialist(value, 'retrieval', machine_id='P-001')
                changed['0-a/response.json'] = encode(value)
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.check(changed, portable, pin)
        changed_pin = deepcopy(pin); changed_pin['portable_sha256'] = '0' * 64
        with self.assertRaises(ValueError): self.check(files, portable, changed_pin)


if __name__ == '__main__':
    unittest.main()
