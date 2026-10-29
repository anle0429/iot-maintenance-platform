"""Reject mismatched child identities, changed facts and borrowed standalone traces in coordinator acceptance."""
from copy import deepcopy
import json
import unittest
from agents.delegation_acceptance import verify_coordinator
from tests.test_agent_answer_acceptance import retrieval_case, drift_case


def case():
    """Construct explicit acceptance fixtures; these are never described as native coordinator success."""
    retrieval, r = retrieval_case(); drift, d = drift_case(); children = {}; parts = []
    for role, reply, answer in [('retrieval', retrieval, r), ('drift', drift, d)]:
        native = deepcopy(reply['result']); session = 'child-' + role; request = 'delegated ' + role
        native['contextId'] = session; native['history'].insert(0, {'role': 'user', 'parts': [{'text': request}]})
        children[role] = {'session': {'error': False, 'data': {'session': {'id': session, 'agent_id': 'iot_agents__NS__iot_' + role}}},
            'tasks': {'error': False, 'data': [native]}}
        parts.extend([{'metadata': {'adk_type': 'function_call'}, 'data': {'id': 'call-' + role,
            'name': 'iot_agents__NS__iot_' + role, 'args': {'request': request}}},
            {'metadata': {'adk_type': 'function_response'}, 'data': {'id': 'call-' + role,
            'name': 'iot_agents__NS__iot_' + role, 'response': {'result': json.dumps(answer), 'subagent_session_id': session}}}])
    coordinator = {'result': {'id': 'coordinator-task', 'contextId': 'coordinator-context', 'status': {'state': 'completed'},
        'history': [{'role': 'agent', 'parts': parts}],
        'artifacts': [{'parts': [{'kind': 'text', 'text': json.dumps({'retrieval': r, 'drift': d})}]}]}}
    return coordinator, children


def verify(reply, children):
    """Use the independently specified three-machine fixture window, rather than the live demo cohort."""
    return verify_coordinator(reply, children, window_id='test-window', machine_ids=['P-001', 'P-002', 'P-003'])


class DelegationAcceptanceTests(unittest.TestCase):
    """Accept complete trace agreement and reject each independent attribution/factual mismatch."""

    def test_complete_children_are_checked_independently(self):
        """A combined fixture passes only after both children's own tool histories agree with their answers."""
        reply, children = case(); result = verify(reply, children)
        self.assertTrue(result['coordinator_delegation_verified'])
        self.assertFalse(result['coursework_complete'])
        self.assertEqual(result['children']['retrieval']['successful_tool_events'], 3)

    def test_protocol_and_pairing_failures(self):
        """Reject failed tasks, unknown delegates and missing or contradictory call/response identities."""
        for mode in ['error', 'missing-task', 'failed', 'unattributed', 'foreign-tool', 'unpaired', 'missing-child', 'conflict', 'tool-error']:
            reply, children = case(); task = reply['result']; parts = task['history'][0]['parts']
            if mode == 'error': reply['error'] = {'message': 'failed'}
            elif mode == 'missing-task': reply['result'] = None
            elif mode == 'failed': task['status']['state'] = 'failed'
            elif mode == 'unattributed': parts[0]['data'].pop('id')
            elif mode == 'foreign-tool': parts[0]['data']['name'] = 'unknown'
            elif mode == 'unpaired': parts[1]['data']['id'] = 'other-call'
            elif mode == 'missing-child': children.pop('drift')
            elif mode == 'conflict':
                bad = deepcopy(parts[0]); bad['data']['args']['request'] = 'changed'; parts.append(bad)
            else: parts[1]['data']['response']['error'] = 'native failure'
            with self.subTest(mode=mode), self.assertRaises(ValueError): verify(reply, children)

    def test_child_identity_and_actual_request_are_required(self):
        """A similarly worded standalone answer cannot substitute for the actual delegated session and request."""
        for mode in ['session', 'agent', 'parent', 'no-task', 'two-tasks', 'request', 'api-error']:
            reply, children = case(); child = children['retrieval']; tasks = child['tasks']['data']
            if mode == 'session': child['session']['data']['session']['id'] = 'borrowed-session'
            elif mode == 'agent': child['session']['data']['session']['agent_id'] = 'another-agent'
            elif mode == 'parent': reply['result']['contextId'] = 'child-retrieval'
            elif mode == 'no-task': tasks.clear()
            elif mode == 'two-tasks': tasks.append(deepcopy(tasks[0]))
            elif mode == 'request': tasks[0]['history'][0]['parts'][0]['text'] = 'unrelated question'
            else: child['tasks']['error'] = True
            with self.subTest(mode=mode), self.assertRaises(ValueError): verify(reply, children)

    def test_changed_answers_or_tool_facts_are_rejected(self):
        """Reject altered coordinator text, child artifacts and underlying model facts independently."""
        for mode in ['combined', 'delegated', 'child', 'native-tool']:
            reply, children = case(); task = reply['result']; parts = task['history'][0]['parts']
            if mode == 'combined': task['artifacts'][0]['parts'][0]['text'] = '{"retrieval":{},"drift":{}}'
            elif mode == 'delegated': parts[1]['data']['response']['result'] = '{"risk_score":0.99}'
            elif mode == 'child': children['retrieval']['tasks']['data'][0]['artifacts'][0]['parts'][0]['text'] = '{}'
            else:
                history = children['retrieval']['tasks']['data'][0]['history']
                for message in history:
                    for part in message.get('parts', []):
                        data = part.get('data', {})
                        if data.get('name') == 'predict_failure' and 'response' in data:
                            value = json.loads(data['response']['output']); value['prediction']['risk_score'] = 0.99
                            data['response']['output'] = json.dumps(value)
            with self.subTest(mode=mode), self.assertRaises(ValueError): verify(reply, children)


if __name__ == '__main__':
    unittest.main()
