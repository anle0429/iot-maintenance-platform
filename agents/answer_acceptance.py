"""Check compact specialist answers against paired native tool events, rather than A2A completion alone."""
from hashlib import sha256
from math import isclose, isfinite
from pathlib import Path
import json


KNOWLEDGE = Path(__file__).resolve().parents[1] / 'knowledge'
TOOLS = {'retrieval': {'get_machine_features', 'predict_failure', 'search_knowledge'},
         'drift': {'assess_feature_drift'}}


def unique_object(pairs):
    """Reject ambiguous JSON objects instead of silently accepting the last duplicate field."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field: ' + key)
        result[key] = value
    return result


def final_answer(task):
    """Read only the final artifact, excluding prompts, intermediate text and tool responses."""
    if task.get('status', {}).get('state') != 'completed':
        raise ValueError('The native task did not complete')
    text = ''.join(part.get('text', '') for artifact in task.get('artifacts', [])
                   for part in artifact.get('parts', []) if part.get('kind') == 'text').strip()
    # A single Markdown code fence is presentation only; prose outside it is rejected.
    if text.startswith('```json\n') and text.endswith('\n```'):
        text = text[len('```json\n'):-len('\n```')]
    try:
        answer = json.loads(text, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError('The final artifact is not one complete, unambiguous JSON answer') from error
    if not isinstance(answer, dict):
        raise ValueError('The final answer must be an object')
    return answer


def native_tools(task, role):
    """Pair successful native outputs with actual call IDs; preserve retries without counting them as new machines."""
    if role not in TOOLS:
        raise ValueError('Specialist acceptance does not establish coordinator delegation')
    calls = {}; results = {}; successful = 0; errors = 0
    for message in task.get('history', []):
        if message.get('role') != 'agent':
            continue
        for part in message.get('parts', []):
            kind = part.get('metadata', {}).get('adk_type'); data = part.get('data', {})
            if kind not in {'function_call', 'function_response'}:
                continue
            name = data.get('name'); identity = data.get('id')
            if name not in TOOLS[role] or not identity:
                raise ValueError('Unexpected or unattributed native tool event')
            key = (name, identity)
            if kind == 'function_call':
                arguments = data.get('args')
                if isinstance(arguments, dict) and isinstance(arguments.get('request'), dict):
                    if key in calls and calls[key] != arguments['request']:
                        raise ValueError('A native call ID has conflicting arguments')
                    calls[key] = arguments['request']
                continue
            response = data.get('response', {})
            if not isinstance(response, dict) or 'error' in response:
                errors += 1
                continue
            if key not in calls or 'output' not in response:
                raise ValueError('A tool output has no paired native request')
            output = response['output']
            if isinstance(output, str):
                output = json.loads(output, object_pairs_hook=unique_object)
            if not isinstance(output, dict):
                raise ValueError('A native tool result must be an object')
            observed = {'arguments': calls[key], 'output': output}
            if name in results and results[name] != observed:
                raise ValueError('Repeated tool calls returned contradictory snapshots')
            results[name] = observed; successful += 1
    if set(results) != TOOLS[role]:
        raise ValueError('Required native tool results are missing')
    return results, {'successful_tool_events': successful, 'tool_error_events': errors}


def same_fields(answer, expected):
    """Require the declared answer fields and exact identities, rejecting extra unsupported readings."""
    if set(answer) != set(expected):
        raise ValueError('The answer has missing or unsupported fields')
    for key, value in expected.items():
        actual = answer[key]
        if type(actual) is not type(value) or actual != value:
            raise ValueError('The answer disagrees with the native result: ' + key)


def check_retrieval(answer, tools, machine_id, knowledge_root):
    """Bind the risk to the same feature snapshot and every citation to actual authored source bytes."""
    feature = tools['get_machine_features']['output']; prediction = tools['predict_failure']['output']
    knowledge = tools['search_knowledge']['output']
    for name in ['get_machine_features', 'predict_failure']:
        if tools[name]['arguments'] != {'machine_id': machine_id}:
            raise ValueError('The native request addresses another machine')
    if feature['machine_id'] != machine_id or prediction['feature_trace'] != feature:
        raise ValueError('Prediction and features do not share the same machine snapshot')
    if (feature.get('clock_mode') != 'simulation'
            or prediction['model'].get('score_kind') != 'uncalibrated_class_weighted_logistic'):
        raise ValueError('This demo contract requires the declared simulated, uncalibrated model')
    matches = knowledge.get('matches', [])
    if not matches:
        raise ValueError('No native passages support the answer')
    root = knowledge_root.resolve()
    for chunk in matches:
        source = (root / chunk['source_file']).resolve()
        if not source.is_relative_to(root):
            raise ValueError('A citation escapes the knowledge source directory')
        raw = source.read_bytes()
        if sha256(raw).hexdigest() != chunk['source_sha256'] or chunk['text'] not in raw.decode('utf-8'):
            raise ValueError('A cited passage disagrees with the original source')
    expected = {'machine_id': machine_id, 'feature_event_ts': feature['feature_event_ts'],
        'model_version': prediction['model']['version'], 'document_ids': sorted({c['document_id'] for c in matches}),
        'vibration_alone_proves_failure': False, 'simulated': True, 'score_is_calibrated': False}
    score = answer.get('risk_score')
    if (type(score) not in {int, float} or not isfinite(score)
            or not isclose(score, prediction['prediction']['risk_score'], rel_tol=0, abs_tol=0.0000005)):
        raise ValueError('Risk score is missing, non-finite or disagrees with the native prediction')
    other = {k: v for k, v in answer.items() if k != 'risk_score'}
    # Document order has no business meaning, but repeated or unrequested IDs do.
    if isinstance(other.get('document_ids'), list):
        other['document_ids'] = sorted(other['document_ids'])
    same_fields(other, expected)


def check_drift(answer, tools, window_id, machine_ids):
    """Compare the exact cohort, flagged features and reference identities without counting retries twice."""
    native = tools['assess_feature_drift']; args = native['arguments']; value = native['output']
    ids = args.get('machine_ids', [])
    if (args.get('window_id') != window_id or len(ids) != len(set(ids))
            or set(ids) != set(machine_ids) or value['unique_rows'] != len(set(machine_ids))
            or value['window_id'] != window_id):
        raise ValueError('The drift result addresses a different or duplicate cohort/window')
    expected = {'window_id': window_id, 'unique_rows': value['unique_rows'],
        'flagged_features': sorted(k for k, v in value['features'].items() if v['drifted']),
        'reference_id': value['reference_id'], 'assessment_sha256': value['assessment_sha256'],
        'drift_establishes_accuracy_loss': False, 'drift_establishes_failure': False}
    normalized = dict(answer)
    if isinstance(normalized.get('flagged_features'), list):
        normalized['flagged_features'] = sorted(normalized['flagged_features'])
    same_fields(normalized, expected)


def verify_specialist(reply, role, *, machine_id='P-001', window_id='agent-demo-29', machine_ids=None,
                      knowledge_root=KNOWLEDGE):
    """Accept the bounded demo contract only; native provenance still requires a separately captured run/source seal."""
    if reply.get('error') or not isinstance(reply.get('result'), dict):
        raise ValueError('A2A returned an error instead of a native task')
    task = reply['result']
    if not task.get('id') or not task.get('contextId'):
        raise ValueError('The task has no attributable task/context identity')
    answer = final_answer(task); tools, counts = native_tools(task, role)
    if role == 'retrieval':
        check_retrieval(answer, tools, machine_id, knowledge_root)
    else:
        cohort = machine_ids if machine_ids is not None else [f'P-{i:03}' for i in range(1, 33)]
        check_drift(answer, tools, window_id, cohort)
    return {'passed': True, 'role': role, 'task_id': task.get('id'), 'context_id': task.get('contextId'),
        **counts, 'coordinator_delegation_verified': False, 'coursework_complete': False,
        'scope': 'Final compact specialist answer equals its paired captured tool results. No claim of live deployment, source-seal authenticity, coordinator delegation or full coursework completion.'}
