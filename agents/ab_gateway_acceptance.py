"""Independently join paired native answers, Substrate worker ownership and gateway wire records."""
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
from ipaddress import ip_address
import importlib.util
import json
from math import isfinite
from pathlib import Path
import re

from agents.ab_configuration import VARIANTS, comparison_summary
from agents.answer_acceptance import KNOWLEDGE, native_tools, unique_object, verify_specialist
from agents.probe import QUESTIONS

ROOT = Path(__file__).resolve().parents[1]
ORDER = (('a', 'P-001'), ('b', 'P-001'), ('b', 'P-002'),
         ('a', 'P-002'), ('a', 'P-003'), ('b', 'P-003'))
CASE_FILES = ('request.json', 'response.json', 'outcome.json', 'gateway-before.log',
              'gateway-after.log', 'actor.json', 'worker-pods.json', 'answer-acceptance.json')
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def require(condition, message):
    """Refuse incomplete or contradictory evidence without depending on Python assertions."""
    if not condition:
        raise ValueError(message)


def aware(value):
    """Require a native timezone-aware clock and normalize it to UTC."""
    require(isinstance(value, str), 'Native timestamp is not a string')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('Malformed native timestamp') from error
    require(result.tzinfo is not None, 'Native timestamp lacks a timezone')
    return result.astimezone(timezone.utc)


def decode(raw):
    """Reject duplicate JSON keys and non-finite constants in original native bodies."""
    require(isinstance(raw, bytes), 'Require original native bytes')
    def reject_constant(_value):
        """Non-finite JSON constants cannot describe native clocks or measurements."""
        raise ValueError('Non-finite JSON constant')
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)


def actor_name(agent, context):
    """Apply the pinned controller name-length rule to the actual full agent name and context."""
    candidate = 'asr-iot-agents-' + agent + '-' + context.strip().lower().replace('_', '-')
    return candidate if len(candidate) <= 63 else 'asr-' + sha256(
        ('iot-agents/' + agent + '/' + context).encode()).hexdigest()[:24]


def actor_worker(actor, pods, agent, context):
    """Join a Running native actor assignment to the actual Ready retrieval worker Pod UID and IP."""
    metadata = actor['metadata']
    require(metadata.get('atespace') == 'iot-agents'
            and metadata.get('name') == actor_name(agent, context)
            and isinstance(metadata.get('uid'), str) and metadata['uid']
            and actor.get('status') == 'STATUS_RUNNING'
            and actor.get('actorTemplateNamespace') == 'iot-agents'
            and re.fullmatch(re.escape(agent) + r'-[0-9a-f]{16}', actor.get('actorTemplateName', ''))
            and actor.get('ateomPodNamespace') == 'iot-agents'
            and actor.get('workerPoolName') == 'iot-retrieval-pool',
            'The Running actor is not attributable to this variant and context')
    require(pods.get('apiVersion') == 'v1' and pods.get('kind') == 'PodList'
            and isinstance(pods.get('items'), list), 'Require the original worker PodList')
    matches = []; seen_names = set(); seen_uids = set(); seen_ips = set()
    for pod in pods['items']:
        require(isinstance(pod, dict) and pod.get('apiVersion') == 'v1' and pod.get('kind') == 'Pod',
                'Malformed native worker Pod')
        item = pod['metadata']; status = pod.get('status', {})
        name, uid = item.get('name'), item.get('uid')
        require(item.get('namespace') == 'iot-agents' and isinstance(name, str) and name
                and isinstance(uid, str) and uid and name not in seen_names and uid not in seen_uids,
                'Duplicate or unidentified native worker Pod')
        seen_names.add(name); seen_uids.add(uid)
        if status.get('phase') == 'Running' and not item.get('deletionTimestamp'):
            address = str(ip_address(status['podIP']))
            require(address not in seen_ips, 'Duplicate Running worker Pod IP')
            seen_ips.add(address)
        if name == actor.get('ateomPodName'):
            require(uid == actor.get('ateomPodUid') and not item.get('deletionTimestamp')
                    and status.get('phase') == 'Running'
                    and status.get('podIP') == actor.get('ateomPodIp')
                    and item.get('labels', {}).get('ate.dev/worker-pool') == 'iot-retrieval-pool'
                    and any(c.get('type') == 'Ready' and c.get('status') == 'True'
                            for c in status.get('conditions', [])),
                    'Actor assignment disagrees with the actual Ready retrieval worker')
            matches.append({'name': name, 'uid': uid, 'ip': str(ip_address(status['podIP']))})
    require(len(matches) == 1, 'Actor worker is missing or ambiguous in the native PodList')
    return {'actor': metadata['name'], 'actor_uid': metadata['uid'], **matches[0]}


def socket_ip(value):
    """Read a native IP:port socket without accepting an invented source label."""
    require(isinstance(value, str), 'Missing native source socket')
    host, port = value.rsplit(':', 1)
    if host.startswith('[') and host.endswith(']'):
        host = host[1:-1]
    require(port.isascii() and port.isdecimal() and 1 <= int(port) <= 65535,
            'Malformed native source socket port')
    return str(ip_address(host))


def appended_records(before, after):
    """Subtract only an exact full same-Pod log prefix; never filter native errors or malformed lines."""
    require(isinstance(before, bytes) and isinstance(after, bytes) and after.startswith(before)
            and (not before or before.endswith(b'\n')) and after.endswith(b'\n'),
            'Gateway logs are not complete lines with an exact unchanged prefix')
    rows = [decode(line) for line in after[len(before):].splitlines() if line.strip()]
    require(rows and all(isinstance(row, dict) for row in rows), 'No original appended gateway records')
    return rows


def wire_acceptance(records, variant, context, worker, task, started, finished):
    """Require this task's operator ingress, all three MCP tools and model traffic from its actual actor worker."""
    agent = VARIANTS[variant]['agent']; path = '/api/a2a-sandboxes/iot-agents/' + agent
    ingress = []; mcp = []; models = []; non_business = []
    for row in records:
        require(started <= aware(row.get('time')) <= finished,
                'Gateway record is outside the original request interval')
        status = row.get('http.status')
        require(type(status) is int and 200 <= status < 300,
                'An unexpected gateway error remains in this request window')
        route = row.get('route')
        require(isinstance(route, str), 'Missing native gateway route')
        suffix = route.rsplit('/', 1)[-1]
        if suffix in {'a2a-ab-retrieval-' + variant, 'a2a-ab-retrieval-' + variant + '-slash'}:
            expected = path + ('/' if suffix.endswith('-slash') else '')
            if row.get('http.method') == 'GET':
                require(row.get('http.path') == path + '/.well-known/agent-card.json',
                        'A/B card uses another endpoint')
                non_business.append(row); continue
            require(row.get('http.method') == 'POST' and row.get('http.path') == expected
                    and status == 200 and row.get('a2a.method') == 'message/send'
                    and row.get('a2a.context.id') == context
                    and row.get('a2a.response.outcome') == 'success'
                    and row.get('a2a.result.kind') == 'task' and row.get('a2a.task.state') == 'completed',
                    'Gateway operator ingress does not prove this completed A/B task')
            ingress.append(row)
        elif suffix == 'proxy-mcp-retrieval':
            require(row.get('http.path') == '/mcp/retrieval'
                    and socket_ip(row.get('src.addr')) == worker['ip'],
                    'MCP traffic is not on the selected path from the actual actor worker')
            method = row.get('mcp.method.name')
            if method == 'tools/call':
                require(row.get('http.method') == 'POST' and status == 200
                        and row.get('protocol') == 'mcp' and row.get('mcp.target') == 'retrieval'
                        and row.get('gen_ai.tool.name') in {'get_machine_features', 'predict_failure', 'search_knowledge'}
                        and isinstance(row.get('mcp.session.id'), str) and 0 < len(row['mcp.session.id']) <= 4096,
                        'Unexpected MCP tool execution or missing native session')
                mcp.append(row)
            else:
                require((row.get('http.method') == 'POST' and method in {
                        'initialize', 'notifications/initialized', 'tools/list', 'ping'})
                        or row.get('http.method') in {'GET', 'DELETE'} and method is None,
                        'Unexpected MCP protocol request in the retained window')
                non_business.append(row)
        elif suffix == 'model':
            require(row.get('http.method') == 'POST' and row.get('http.path') == '/v1/chat/completions'
                    and status == 200 and socket_ip(row.get('src.addr')) == worker['ip']
                    and re.fullmatch(r'[0-9a-f]{32}', row.get('observer_request_id', '')),
                    'Generation is not attributable to the actor worker through the model route')
            models.append(row)
        else:
            raise ValueError('Unattributed or unexpected gateway route in this request window')
    require(len(ingress) == 1 and models, 'Require one completed operator task and actual routed generation')
    expected = Counter()
    for message in task.get('history', []):
        if message.get('role') != 'agent': continue
        for part in message.get('parts', []):
            if part.get('metadata', {}).get('adk_type') == 'function_response':
                expected[part['data']['name']] += 1
    require(Counter(row['gen_ai.tool.name'] for row in mcp) == expected,
            'Gateway tool execution counts disagree with the complete native MCP history')
    observer_ids = [row['observer_request_id'] for row in models]
    require(len(observer_ids) == len(set(observer_ids)), 'A model observer ID was repeated')
    return {'operator_ingress': ingress[0], 'mcp_events': len(mcp), 'model_requests': len(models),
            'observer_request_ids': observer_ids, 'protocol_records': len(non_business),
            'gateway_records': len(records), 'worker': worker}


@lru_cache(maxsize=1)
def prediction_oracle():
    """Use the independent portable-model arithmetic already used by the original accepted A/B verifier."""
    spec = importlib.util.spec_from_file_location('ab_gateway_prediction_oracle', ROOT / 'scripts/demo-feature-api.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.check_prediction


def verify_ab_comparison(files, *, portable_bytes, model_pin, knowledge_root=KNOWLEDGE):
    """Verify all six original requests; payload authenticity and source pins belong to the outer capsule verifier."""
    try:
        return _verify_ab_comparison(files, portable_bytes, model_pin, knowledge_root)
    except (KeyError, TypeError, AttributeError, IndexError, UnicodeError) as error:
        raise ValueError('Malformed or incomplete native A/B gateway evidence') from error


def _verify_ab_comparison(files, portable_bytes, model_pin, knowledge_root):
    """Keep fixed pair order, native answers, actual worker joins and independent prediction checks together."""
    require(isinstance(files, dict) and isinstance(portable_bytes, bytes), 'Require native file bytes and portable artifact')
    require(model_pin.get('version') == '6' and sha256(portable_bytes).hexdigest() == model_pin.get('portable_sha256'),
            'Portable model bytes do not match the selected model6 pin')
    model = decode(portable_bytes); rows = []; cases = {}; contexts = set(); ids = set(); task_ids = set()
    observer_ids = set(); maximum_error = 0.; previous_finish = None
    for index, (variant, machine) in enumerate(ORDER):
        label = str(index // 2) + '-' + variant
        raw = {name: files[label + '/' + name] for name in CASE_FILES}
        request = decode(raw['request.json']); response = decode(raw['response.json']); outcome = decode(raw['outcome.json'])
        context = request['params']['message']['contextId']; request_id = request['id']; task = response['result']
        require(isinstance(context, str) and UUID.fullmatch(context) and context not in contexts
                and isinstance(request_id, str) and UUID.fullmatch(request_id) and request_id not in ids,
                'Require fresh attributable A2A context and request UUIDs')
        contexts.add(context); ids.add(request_id)
        require(request.get('jsonrpc') == response.get('jsonrpc') == '2.0'
                and request.get('method') == 'message/send' and response.get('id') == request_id
                and request['params']['configuration'] == {'blocking': True}
                and request['params']['message']['role'] == 'user'
                and request['params']['message']['parts'] == [{'kind': 'text',
                    'text': QUESTIONS['retrieval'].replace('P-001', machine)}]
                and task.get('contextId') == context, 'Native A/B request, task or workload differs')
        accepted = verify_specialist(response, 'retrieval', machine_id=machine, knowledge_root=knowledge_root)
        require(decode(raw['answer-acceptance.json']) == accepted and accepted['tool_error_events'] == 0,
                'Original answer acceptance or MCP errors disagree with the native response')
        require(task['id'] not in task_ids, 'A native task was reused across comparison cases'); task_ids.add(task['id'])
        require(outcome.get('variant') == variant and outcome.get('machine_id') == machine
                and outcome.get('context_id') == context and outcome.get('request_id') == request_id
                and outcome.get('http_status') == 200 and outcome.get('response_complete') is True
                and outcome.get('passed') is True and outcome.get('task_id') == task['id']
                and outcome.get('successful_tool_events') == accepted['successful_tool_events']
                and outcome.get('tool_error_events') == accepted['tool_error_events'],
                'Native outcome does not describe its exact accepted request')
        started, finished = aware(outcome['started_utc']), aware(outcome['finished_utc'])
        elapsed = outcome.get('elapsed_seconds')
        require(type(elapsed) in {int, float} and isfinite(elapsed) and elapsed > 0
                and finished > started and abs((finished - started).total_seconds() - elapsed) < 2
                and (previous_finish is None or started >= previous_finish),
                'Require finite original clocks and six sequential non-overlapping requests')
        previous_finish = finished
        task_time = aware(task['status']['timestamp'])
        require(started <= task_time <= finished, 'Task completion is outside its original request clocks')
        tools, _ = native_tools(task, 'retrieval')
        prediction = tools['predict_failure']['output']; trace = tools['get_machine_features']['output']
        expected = prediction_oracle()(trace, prediction, model, model_pin, task_time)
        maximum_error = max(maximum_error, abs(expected - prediction['prediction']['risk_score']))
        worker = actor_worker(decode(raw['actor.json']), decode(raw['worker-pods.json']), VARIANTS[variant]['agent'], context)
        records = appended_records(raw['gateway-before.log'], raw['gateway-after.log'])
        wire = wire_acceptance(records, variant, context, worker, task, started, finished)
        require(not observer_ids.intersection(wire['observer_request_ids']), 'Model request IDs were reused across cases')
        observer_ids.update(wire['observer_request_ids']); rows.append(outcome)
        cases[label] = {'variant': variant, 'machine_id': machine, 'context_id': context,
            'task_id': task['id'], 'answer': accepted, 'wire': wire,
            'input_sha256': {name: sha256(value).hexdigest() for name, value in raw.items()}}
    return {'passed': True, 'requests': len(rows), 'summary': comparison_summary(rows, ['P-001', 'P-002', 'P-003']),
        'successful_mcp_events': sum(case['wire']['mcp_events'] for case in cases.values()),
        'model_requests': len(observer_ids), 'independent_prediction_max_error': maximum_error, 'cases': cases,
        'scope': 'Six sequential paired requests: final native answers and pinned model6 arithmetic agree with '
                 'the captured task histories, actual Running actor-to-worker assignments and exact appended '
                 'gateway operator/MCP/model logs. Authenticity requires the separately pinned native capsule; '
                 'no general quality ranking, speedup, automatic promotion or whole-platform readiness claim.'}
