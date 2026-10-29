"""Verify coordinator answers against the actual delegated child sessions and their native MCP results."""
import json
from agents.answer_acceptance import KNOWLEDGE, final_answer, unique_object, verify_specialist

DELEGATES = {'iot_agents__NS__iot_retrieval': 'retrieval', 'iot_agents__NS__iot_drift': 'drift'}


def verify_coordinator(reply, children, *, knowledge_root=KNOWLEDGE, machine_id='P-001',
                       window_id='agent-demo-29', machine_ids=None):
    """Require paired A2A calls, controller-recorded child identities and exact preservation of verified answers."""
    if reply.get('error') or not isinstance(reply.get('result'), dict):
        raise ValueError('The coordinator has no native task')
    task = reply['result']; answer = final_answer(task)
    if not task.get('id') or not task.get('contextId') or set(answer) != {'retrieval', 'drift'}:
        raise ValueError('Require one attributable combined coordinator answer')
    calls = {}; responses = {}
    for message in task.get('history', []):
        if message.get('role') != 'agent': continue
        for part in message.get('parts', []):
            kind = part.get('metadata', {}).get('adk_type'); data = part.get('data', {})
            if kind not in {'function_call', 'function_response'}: continue
            role = DELEGATES.get(data.get('name')); identity = data.get('id')
            if not role or not identity: raise ValueError('Unexpected or unattributed coordinator tool event')
            key = (role, identity)
            if kind == 'function_call':
                request = data.get('args', {}).get('request')
                if not isinstance(request, str) or not request: raise ValueError('Missing delegated request')
                if key in calls and calls[key] != request: raise ValueError('Conflicting delegated request')
                calls[key] = request
            else:
                response = data.get('response', {})
                if key not in calls or 'error' in response: raise ValueError('Unpaired or failed delegated result')
                if key in responses and responses[key] != response: raise ValueError('Conflicting delegated result')
                responses[key] = response
    if (len(calls) != 2 or set(calls) != set(responses)
            or {role for role, _ in calls} != {'retrieval', 'drift'} or set(children) != {'retrieval', 'drift'}):
        raise ValueError('Require one actual completed delegation to each specialist')
    verified = {}
    for (role, identity), response in responses.items():
        session_id = response.get('subagent_session_id')
        child = children[role]; session_response = child['session']; tasks_response = child['tasks']
        if session_response.get('error') or tasks_response.get('error'):
            raise ValueError('The native child-session API reported an error')
        session = session_response['data']['session']
        if (not session_id or session.get('id') != session_id
                or session.get('agent_id') != 'iot_agents__NS__iot_' + role
                or session_id == task['contextId']):
            raise ValueError('The child identity disagrees with the actual delegation')
        tasks = tasks_response['data']
        selected = [t for t in tasks if t.get('contextId') == session_id and t.get('status', {}).get('state') == 'completed']
        if len(selected) != 1: raise ValueError('Require one completed native task for the child session')
        native = selected[0]
        user_text = [''.join(p.get('text', '') for p in m.get('parts', [])) for m in native.get('history', []) if m.get('role') == 'user']
        if calls[(role, identity)] not in user_text: raise ValueError('The child did not receive the captured delegated request')
        checked = verify_specialist({'result': native}, role, knowledge_root=knowledge_root,
            machine_id=machine_id, window_id=window_id, machine_ids=machine_ids)
        delegated = json.loads(response['result'], object_pairs_hook=unique_object)
        if final_answer(native) != delegated or answer[role] != delegated:
            raise ValueError('The coordinator changed or fabricated a child answer')
        verified[role] = checked
    return {'passed': True, 'task_id': task['id'], 'context_id': task['contextId'],
        'coordinator_delegation_verified': True, 'children': verified, 'coursework_complete': False,
        'scope': 'Combined answer equals two actual delegated child answers, each verified against its own MCP history. Native capture authenticity remains separately required.'}
