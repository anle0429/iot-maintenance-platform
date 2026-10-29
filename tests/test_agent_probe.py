"""Check that native-agent clients preserve failed protocol outcomes instead of accepting fluent or partial replies."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from agents.probe import run, request, TransportFailure


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

        def capture(opener, url, payload=None, user_id=None, timeout=900):
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

    def test_incomplete_http_body_is_retained_as_failure(self):
        """An observed200status with a cut JSON body must not become a completed response."""
        from http.client import IncompleteRead
        response = unittest.mock.Mock(); response.status = 200
        response.read.side_effect = IncompleteRead(b'{"result":', 200)
        opener = unittest.mock.MagicMock(); opener.open.return_value.__enter__.return_value = response
        with self.assertRaises(TransportFailure) as caught:
            request(opener, 'http://private-controller')
        self.assertEqual(caught.exception.status, 200)
        self.assertEqual(caught.exception.partial, b'{"result":')
        self.assertEqual(caught.exception.error_type, 'IncompleteRead')

    def test_timeout_has_no_fabricated_response(self):
        """A network deadline without response headers remains a failed attempt with no invented status/body."""
        opener = unittest.mock.Mock(); opener.open.side_effect = TimeoutError('private detail is not a receipt')
        with self.assertRaises(TransportFailure) as caught:
            request(opener, 'http://private-controller', timeout=1200)
        self.assertIsNone(caught.exception.status); self.assertEqual(caught.exception.partial, b'')
        self.assertNotIn('private detail', str(caught.exception))
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 1200)

    def test_run_records_partial_transport_and_role_budget(self):
        """Save a coordinator deadline and its partial wire bytes while leaving completion unverified."""
        calls = []

        def capture(opener, url, payload=None, user_id=None, timeout=900):
            """Supply identity/card fixtures and an explicit incomplete native-transport-shaped failure."""
            if url.endswith('/api/me'): return 200, b'{"sub":"test-identity"}', .01
            if payload is None: return 200, b'{}', .01
            calls.append(timeout)
            raise TransportFailure('IncompleteRead', 2100, 200, b'{"result":')

        with tempfile.TemporaryDirectory() as directory, patch('agents.probe.request', side_effect=capture):
            target = Path(directory) / 'result'
            with self.assertRaisesRegex(ValueError, 'transport did not complete'):
                run('http://private-controller', target, ['coordinator'])
            self.assertEqual(calls, [2100])
            self.assertEqual((target / 'coordinator-response.partial').read_bytes(), b'{"result":')
            self.assertFalse((target / 'coordinator-response.json').exists())
            self.assertFalse((target / 'verification.json').exists())
            transport = json.loads((target / 'coordinator-transport.json').read_bytes())
            self.assertFalse(transport['response_complete'])
            self.assertEqual(transport['error_type'], 'IncompleteRead')

    def test_unbounded_or_invalid_http_wait_is_rejected(self):
        """A test driver cannot silently request an unlimited wait outside its documented bound."""
        for value in [0, -1, 2401, True, float('nan'), float('inf')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                request(unittest.mock.Mock(), 'http://private-controller', timeout=value)
