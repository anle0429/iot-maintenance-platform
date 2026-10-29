"""Check that native-agent clients preserve failed protocol outcomes instead of accepting fluent or partial replies."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from agents.probe import run, request


class AgentProbeTests(unittest.TestCase):
    """Exercise transport rejection, session attribution and task completion boundaries without a model stub claiming native proof."""

    def test_failed_task_with_tool_history_is_not_success(self):
        """A task that called tools but failed during generation must remain a failed acceptance result."""
        replies = [(200, b'{"sub":"test-identity"}', .01), (200, b'{}', .01),
            (200, json.dumps({'result': {'id': 'task-1', 'contextId': 'context-1', 'history': [{'role': 'agent'}],
                'status': {'state': 'failed'}}}).encode(), 2)]
        with tempfile.TemporaryDirectory() as directory, patch('agents.probe.request', side_effect=replies):
            target = Path(directory) / 'result'
            with self.assertRaisesRegex(ValueError, 'did not complete'):
                run('http://private-controller', target, ['retrieval'])
            self.assertEqual(json.loads((target / 'results.json').read_bytes())[0]['a2a_state'], 'failed')
            self.assertFalse((target / 'verification.json').exists())

    def test_input_required_preserves_incomplete_status(self):
        """A request for more user input is a real protocol outcome but does not meet complete-demo acceptance."""
        replies = [(200, b'{"sub":"test-identity"}', .01), (200, b'{}', .01),
            (200, b'{"result":{"status":{"state":"input-required"}}}', .02)]
        with tempfile.TemporaryDirectory() as directory, patch('agents.probe.request', side_effect=replies):
            target = Path(directory) / 'result'
            self.assertEqual(run('http://private-controller', target, ['retrieval']), 1)
            self.assertFalse(json.loads((target / 'verification.json').read_bytes())['passed'])

    def test_wire_error_keeps_original_body_and_status(self):
        """Plain HTTP negotiation errors are retained before JSON parsing fails."""
        replies = [(200, b'{"sub":"test-identity"}', .01), (200, b'{}', .01),
                   (400, b'unsupported A2A version', .05)]
        with tempfile.TemporaryDirectory() as directory, patch('agents.probe.request', side_effect=replies):
            target = Path(directory) / 'result'
            with self.assertRaisesRegex(ValueError, 'HTTP 400'):
                run('http://private-controller', target, ['retrieval'])
            self.assertEqual((target / 'retrieval-response.json').read_bytes(), replies[2][1])
            self.assertEqual(json.loads((target / 'retrieval-transport.json').read_bytes())['http_status'], 400)

    def test_completed_request_uses_reported_identity_and_new_session(self):
        """Session IDs and the controller-reported test identity travel with the real A2A request."""
        calls = []

        def capture(opener, url, payload=None, user_id=None):
            """Return protocol-shaped fixtures while retaining inputs for independent session checks."""
            calls.append((url, payload, user_id))
            if url.endswith('/api/me'):
                return 200, b'{"sub":"reported-identity"}', .01
            if payload is None:
                return 200, b'{}', .01
            return 200, b'{"result":{"id":"task-1","status":{"state":"completed"}}}', .01

        with tempfile.TemporaryDirectory() as directory, patch('agents.probe.request', side_effect=capture):
            self.assertEqual(run('http://private-controller', Path(directory) / 'result', ['retrieval']), 0)
        message = calls[-1][1]['params']['message']
        self.assertEqual(calls[-1][2], 'reported-identity')
        self.assertNotEqual(message['contextId'], message['messageId'])
        self.assertEqual(message['role'], 'user')

    def test_transport_records_http_error_body(self):
        """An HTTP exception remains an observed status/body rather than becoming an apparent successful empty reply."""
        import io
        error = HTTPError('http://private-controller', 503, 'Unavailable', {}, io.BytesIO(b'not ready'))
        opener = unittest.mock.Mock(); opener.open.side_effect = error
        status, body, elapsed = request(opener, 'http://private-controller', {'message': 'x'}, user_id='reported-identity')
        self.assertEqual((status, body), (503, b'not ready'))
        self.assertGreaterEqual(elapsed, 0)
        sent = opener.open.call_args.args[0]
        self.assertEqual(sent.get_header('A2a-version'), '0.3')
