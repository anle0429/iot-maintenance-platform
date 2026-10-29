"""Exercise independent known answers, truncated native baselines and adversarial mismatches in specialist acceptance."""
from pathlib import Path
import json
import unittest
from agents.answer_acceptance import final_answer, verify_specialist

ROOT = Path(__file__).resolve().parents[1]
RETRIEVAL_PATH = ROOT / 'docs/evidence/agent-platform/truncated-answer/native/chat/retrieval-response.json'


def retrieval_case():
    """Pair original historical tool events with an explicitly synthetic complete-answer fixture, never native-success evidence."""
    reply = json.loads(RETRIEVAL_PATH.read_bytes())
    answer = {'machine_id': 'P-001', 'feature_event_ts': '2026-11-29T23:50:00Z', 'model_version': '6',
        'risk_score': 0.103181, 'document_ids': ['prediction', 'monitoring', 'assistant-policy'],
        'vibration_alone_proves_failure': False, 'simulated': True, 'score_is_calibrated': False}
    reply['result']['artifacts'] = [{'parts': [{'kind': 'text', 'text': json.dumps(answer)}]}]
    return reply, answer


def drift_case():
    """Build a small independently specified cohort fixture with a retry and an unrelated failed tool envelope."""
    value = {'window_id': 'test-window', 'unique_rows': 3, 'reference_id': 'reference-v1',
        'assessment_sha256': 'a' * 64, 'features': {'rpm': {'drifted': False}, 'current': {'drifted': True}}}
    answer = {'window_id': 'test-window', 'unique_rows': 3, 'flagged_features': ['current'],
        'reference_id': 'reference-v1', 'assessment_sha256': 'a' * 64,
        'drift_establishes_accuracy_loss': False, 'drift_establishes_failure': False}
    parts = []
    for identity in ['call-one', 'call-two']:
        parts.extend([
            {'metadata': {'adk_type': 'function_call'}, 'data': {'name': 'assess_feature_drift', 'id': identity,
                'args': {'request': {'machine_ids': ['P-003', 'P-001', 'P-002'], 'window_id': 'test-window'}}}},
            {'metadata': {'adk_type': 'function_response'}, 'data': {'name': 'assess_feature_drift', 'id': identity,
                'response': {'output': json.dumps(value)}}},
        ])
    parts.append({'metadata': {'adk_type': 'function_response'}, 'data': {'name': 'assess_feature_drift',
        'id': 'failed-call', 'response': {'error': 'missing required arguments'}}})
    reply = {'result': {'id': 'task-drift', 'contextId': 'context-drift', 'status': {'state': 'completed'},
        'history': [{'role': 'agent', 'parts': parts}],
        'artifacts': [{'parts': [{'kind': 'text', 'text': json.dumps(answer)}]}]}}
    return reply, answer


def replace_answer(reply, answer):
    """Change only the final user-visible fixture, leaving its independent tool events untouched."""
    reply['result']['artifacts'][0]['parts'][0]['text'] = json.dumps(answer)


class SpecialistAnswerTests(unittest.TestCase):
    """Require attributable tool execution, consistent snapshots and complete final facts while keeping scope narrow."""

    def test_complete_fixture_retains_scope_and_rounding(self):
        """An independently rounded score is accepted without implying coordinator or coursework success."""
        reply, _ = retrieval_case(); result = verify_specialist(reply, 'retrieval')
        self.assertEqual(result['successful_tool_events'], 3)
        self.assertFalse(result['coordinator_delegation_verified'])
        self.assertFalse(result['coursework_complete'])

    def test_original_truncated_native_answer_is_rejected(self):
        """Real tool success and completed protocol state do not repair a cut-off original final artifact."""
        with self.assertRaisesRegex(ValueError, 'complete.*JSON'):
            verify_specialist(json.loads(RETRIEVAL_PATH.read_bytes()), 'retrieval')

    def test_missing_wrong_or_unsupported_answer_facts_fail(self):
        """Reject omitted risk, altered provenance, unsupported citations and overstated safety claims."""
        changes = {'risk_score': [None, True, float('nan'), float('inf'), 0.7], 'model_version': ['7', 6],
            'feature_event_ts': ['2026-11-30T00:00:00Z'], 'machine_id': ['P-002'],
            'document_ids': [['monitoring'], ['monitoring', 'monitoring', 'prediction']],
            'simulated': [False], 'score_is_calibrated': [True], 'vibration_alone_proves_failure': [True]}
        for field, values in changes.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    reply, answer = retrieval_case(); answer[field] = value; replace_answer(reply, answer)
                    with self.assertRaises(ValueError): verify_specialist(reply, 'retrieval')
        for field in ['risk_score', 'model_version']:
            reply, answer = retrieval_case(); del answer[field]; replace_answer(reply, answer)
            with self.assertRaises(ValueError): verify_specialist(reply, 'retrieval')

    def test_unexecuted_or_user_supplied_tool_output_is_not_proof(self):
        """A model request, an unpaired output or user-written history cannot substitute for actual execution events."""
        for mode in ['unexecuted', 'unpaired', 'user']:
            reply, _ = retrieval_case()
            for message in reply['result']['history']:
                if mode == 'user': message['role'] = 'user'
                else:
                    excluded = 'function_response' if mode == 'unexecuted' else 'function_call'
                    message['parts'] = [p for p in message['parts'] if p.get('metadata', {}).get('adk_type') != excluded]
            with self.subTest(mode=mode), self.assertRaises(ValueError): verify_specialist(reply, 'retrieval')

    def test_mixed_prediction_snapshot_or_changed_passage_fails(self):
        """The same machine name is insufficient when prediction features differ or a source passage was altered."""
        for name in ['predict_failure', 'search_knowledge']:
            reply, _ = retrieval_case()
            for message in reply['result']['history']:
                for part in message.get('parts', []):
                    if part.get('metadata', {}).get('adk_type') == 'function_response' and part['data']['name'] == name:
                        value = json.loads(part['data']['response']['output'])
                        if name == 'predict_failure': value['feature_trace']['feature_event_ts'] = 'another-snapshot'
                        else: value['matches'][0]['text'] = 'unrecorded invented passage'
                        part['data']['response']['output'] = json.dumps(value)
            with self.subTest(name=name), self.assertRaises(ValueError): verify_specialist(reply, 'retrieval')

    def test_duplicate_json_keys_and_appended_prose_fail(self):
        """A complete-looking prefix cannot hide conflicting fields or trailing text from the final parser."""
        for text in ['{"risk_score":0.1,"risk_score":0.7}', '{"risk_score":0.1} more text', '[]']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                final_answer({'status': {'state': 'completed'}, 'artifacts': [{'parts': [{'kind': 'text', 'text': text}]}]})
        reply, _ = retrieval_case(); task = reply['result']
        task['artifacts'][0]['parts'][0]['text'] = '```json\n' + task['artifacts'][0]['parts'][0]['text'] + '\n```'
        self.assertTrue(verify_specialist(reply, 'retrieval')['passed'])

    def test_task_failure_and_missing_identity_fail(self):
        """A complete answer remains insufficient when its native task failed or lost its session attribution."""
        for change in ['failed', 'input-required', 'missing-identity']:
            reply, _ = retrieval_case()
            if change == 'missing-identity': del reply['result']['contextId']
            else: reply['result']['status']['state'] = change
            with self.subTest(change=change), self.assertRaises(ValueError): verify_specialist(reply, 'retrieval')

    def test_drift_replay_preserves_unique_count_and_error_history(self):
        """Two equal successful results and a recovered tool error still describe three unique machines."""
        reply, _ = drift_case()
        result = verify_specialist(reply, 'drift', window_id='test-window', machine_ids=['P-001', 'P-002', 'P-003'])
        self.assertEqual(result['successful_tool_events'], 2)
        self.assertEqual(result['tool_error_events'], 1)

    def test_drift_reference_and_interpretation_mismatches_fail(self):
        """Reject changed assessments, false flags and claims that input drift demonstrates accuracy loss or failure."""
        for field, value in [('unique_rows', 6), ('flagged_features', ['rpm']), ('reference_id', 'another-reference'),
                             ('assessment_sha256', 'b' * 64), ('drift_establishes_accuracy_loss', True),
                             ('drift_establishes_failure', True)]:
            reply, answer = drift_case(); answer[field] = value; replace_answer(reply, answer)
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_specialist(reply, 'drift', window_id='test-window', machine_ids=['P-001', 'P-002', 'P-003'])

    def test_changed_native_cohort_or_retry_is_rejected(self):
        """Do not merge different cohorts/windows or quietly select the last contradictory result."""
        for mode in ['duplicate', 'other-window', 'contradictory-retry']:
            reply, _ = drift_case(); parts = reply['result']['history'][0]['parts']
            if mode == 'duplicate': parts[0]['data']['args']['request']['machine_ids'] = ['P-001'] * 3
            elif mode == 'other-window': parts[0]['data']['args']['request']['window_id'] = 'other-window'
            else:
                value = json.loads(parts[3]['data']['response']['output']); value['unique_rows'] = 6
                parts[3]['data']['response']['output'] = json.dumps(value)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                verify_specialist(reply, 'drift', window_id='test-window', machine_ids=['P-001', 'P-002', 'P-003'])

    def test_specialist_receipts_do_not_prove_coordinator_delegation(self):
        """Combined-looking text with specialist events does not satisfy a coordinator acceptance contract."""
        reply, _ = retrieval_case()
        with self.assertRaisesRegex(ValueError, 'coordinator delegation'):
            verify_specialist(reply, 'coordinator')
