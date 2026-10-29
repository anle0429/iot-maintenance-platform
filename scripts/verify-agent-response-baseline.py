"""Reconcile the terminal native chatE timeout, full drift answer and output-capped retrieval without claiming complete agents."""
from hashlib import sha256
import json
import re
import sys
from integration_evidence import ROOT, capsule, read, sources

sys.path.insert(0, str(ROOT))
from agents.answer_acceptance import native_tools

MANIFEST_SHA256 = '604de60cfb47308c047aaf4e6cabb454e93003640bec5b7454ac0fb84089b4e6'


def artifact_text(task):
    """Inspect the original final user-visible artifacts, excluding repeated history text."""
    return ''.join(p.get('text', '') for a in task.get('artifacts', []) for p in a.get('parts', []) if p.get('kind') == 'text')


def verify():
    """Keep successful drift facts separate from a failed overall run and undeployed response-policy changes."""
    base, count = capsule('agent-response-baseline', MANIFEST_SHA256)
    assert count == 22
    original_raw = (base / 'original-capture-inventory.json').read_bytes()
    assert sha256(original_raw).hexdigest() == '58de40bc27e01f102e6f4ad11e32a4ff67920e4f5b9f9dfef60a6eb95651811b'
    original = json.loads(original_raw); assert original['credential_scan_passed']
    curation = read(base, 'curation.json'); archive = read(base, 'original-archive.json')
    assert curation['selected_native_file_count'] == len(curation['selected_native_files']) == 19
    assert curation['selected_bytes_edited'] is False and curation['screenshots_generated'] is False
    assert archive['sha256'] == curation['native_archive_sha256'] == 'cbb29df0ca726342aee9cf4e55b5aeef0e22e130c3a08db05c9379d8c644b7d7'
    assert archive['bytes'] == 3368960 and archive['payload_files'] == 544
    for name in curation['selected_native_files']:
        raw = (base / name).read_bytes(); expected = original['files'][name]
        assert len(raw) == expected['bytes'] and sha256(raw).hexdigest() == expected['sha256']
    sources(base, 'chat')
    unit = (base / 'readback/chat-unit.stdout').read_text()
    assert 'Result=timeout\n' in unit and 'ActiveState=failed\n' in unit and 'ExecMainStatus=15\n' in unit
    assert 'InvocationID=4b75299f2ec6480294945873b4ca9775\n' in unit
    assert 'ExecMainExitTimestamp=Thu 2026-10-29 07:22:36 UTC\n' in unit
    results = read(base, 'chat/native/results.json')
    assert [r['role'] for r in results] == ['retrieval', 'drift']
    assert not (base / 'chat/native/coordinator-response.json').exists()
    assert (base / 'chat/native/coordinator-request.json').is_file()
    tasks = {}
    for row in results:
        role = row['role']; task = read(base, 'chat/native/' + role + '-response.json')['result']; tasks[role] = task
        transport = read(base, 'chat/native/' + role + '-transport.json')
        request = read(base, 'chat/native/' + role + '-request.json')
        assert task['status']['state'] == row['a2a_state'] == 'completed'
        assert task['id'] == row['task_id'] and task['contextId'] == row['context_id'] == request['params']['message']['contextId']
        assert transport['http_status'] == row['http_status'] == 200
        assert transport['elapsed_seconds'] == row['elapsed_seconds']
        assert transport['response_bytes'] == (base / ('chat/native/' + role + '-response.json')).stat().st_size
    retrieval = tasks['retrieval']; rtools, rcounts = native_tools(retrieval, 'retrieval')
    prediction = rtools['predict_failure']['output']; feature = rtools['get_machine_features']['output']
    assert prediction['feature_trace'] == feature and feature['machine_id'] == 'P-001'
    assert prediction['prediction']['risk_score'] == 0.10318092377929837 and prediction['model']['version'] == '6'
    rtext = artifact_text(retrieval)
    assert '0.103' not in rtext and rtext.endswith('Machine age (days): 240 days')
    assert retrieval['metadata']['adk_usage_metadata']['candidatesTokenCount'] == 384
    assert retrieval['metadata']['adk_usage_metadata']['promptTokenCount'] == 3815
    drift = tasks['drift']; tools, counts = native_tools(drift, 'drift'); native = tools['assess_feature_drift']
    value = native['output']; args = native['arguments']; text = artifact_text(drift)
    assert args == {'window_id': 'agent-demo-29', 'machine_ids': [f'P-{i:03}' for i in range(1, 33)]}
    assert counts == {'successful_tool_events': 3, 'tool_error_events': 1}
    assert rcounts == {'successful_tool_events': 3, 'tool_error_events': 0}
    assert value['unique_rows'] == 32 and value['window_id'] == 'agent-demo-29'
    assert value['reference_id'] == 'ac366302a409d2a6a0f7f0442f8aab42c882f81310d072908b9826159b758e93'
    assert value['assessment_sha256'] == 'c23698fc9426a3c56e85cbd074e529a5a1158fe392b4882505f569cfa8dcfa9a'
    for label, expected in [('Reference ID', value['reference_id']), ('Assessment SHA256', value['assessment_sha256']),
                             ('Window ID', value['window_id']), ('Unique Rows', str(value['unique_rows']))]:
        actual = re.search(r'\*\*' + label + r'\*\*: ([^\n]+)', text)
        assert actual and actual.group(1) == expected, label
    block = re.search(r'\*\*Flagged Features\*\*:\n(.*?)\n\n', text, re.S)
    assert block
    assert sorted(re.findall(r'- `([^`]+)`', block.group(1))) == sorted(k for k, v in value['features'].items() if v['drifted'])
    assert 'does not imply a reduction in prediction accuracy or a real pump fault.' in text
    return {'passed': True, 'payload_files': count, 'drift_answer_verified': True,
        'successful_drift_tool_events': 3, 'recovered_drift_tool_errors': 1, 'drift_unique_machines': 32,
        'retrieval_answer_verified': False, 'coordinator_answer_verified': False,
        'complete_agent_chat_verified': False, 'response_policy_0_2_deployed': False, 'coursework_complete': False,
        'recorded_probe_matches_current': sha256((ROOT / 'agents/probe.py').read_bytes()).hexdigest() == read(base, 'chat/source/manifest.json')['files']['agents/probe.py'],
        'scope': 'Original chatE drift facts and limitation match executed tool results. Retrieval omitted risk; coordinator has no response and overall unit timed out. Prepared policy0.2 has no native acceptance yet.'}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
