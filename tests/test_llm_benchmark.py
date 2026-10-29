"""Check that inference measurements require genuine generated content and actual reported token usage."""
from io import BytesIO
import json
import unittest
from unittest.mock import patch
from llm.benchmark import streamed_request


class Response(BytesIO):
    """Provide an HTTP-like SSE body with a successful transport status."""
    status = 200


def event(value):
    """Encode a model-server event using the actual SSE framing accepted by the client."""
    return b'data: ' + json.dumps(value).encode() + b'\n\n'


class InferenceMeasurementTests(unittest.TestCase):
    """Reject success-looking headers and incomplete streams rather than inventing token/timing measurements."""
    def test_role_headers_are_not_first_token_and_usage_is_not_estimated(self):
        """Measure first text after the role-only chunk and retain the server's exact usage."""
        usage = {'prompt_tokens': 11, 'completion_tokens': 3, 'total_tokens': 14}
        raw = event({'choices': [{'delta': {'role': 'assistant'}}]}) + event({'choices': [{'delta': {'content': 'Pump'}}]})
        raw += event({'choices': [], 'usage': usage}) + b'data: [DONE]\n\n'
        with patch('llm.benchmark.urlopen', return_value=Response(raw)), patch('llm.benchmark.time.monotonic', side_effect=[1, 1.25, 2]):
            result = streamed_request('http://private.test', {'stream': True})
        self.assertEqual(result['ttft_seconds'], .25); self.assertEqual(result['elapsed_seconds'], 1)
        self.assertEqual(result['usage'], usage); self.assertEqual(result['text'], 'Pump')

    def test_missing_usage_or_generated_content_is_not_accepted(self):
        """Exercise both successful-HTTP but unmeasurable completion partitions."""
        for body in [event({'choices': [{'delta': {'content': 'text'}}]}), event({'choices': [], 'usage': {'completion_tokens': 1}})]:
            with self.subTest(body=body), patch('llm.benchmark.urlopen', return_value=Response(body)), self.assertRaises(ValueError):
                streamed_request('http://private.test', {})

    def test_stream_error_is_not_counted_as_a_generated_response(self):
        """A 200 transport carrying a model error must fail the benchmark."""
        with patch('llm.benchmark.urlopen', return_value=Response(event({'error': {'message': 'failed'}}))), self.assertRaises(ValueError):
            streamed_request('http://private.test', {})
