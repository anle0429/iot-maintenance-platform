"""Check the demo's independent numerical oracle and its refusal of stale, leaked and wrongly pinned responses."""
from copy import deepcopy
from datetime import datetime, timedelta
from datetime import timezone
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import importlib.util
import io
import json
from pathlib import Path
import unittest
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('feature_demo', ROOT / 'scripts/demo-feature-api.py')
DEMO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEMO)


class FeatureDemoTests(unittest.TestCase):
    """Reuse an original native prediction as input, then alter one safety-relevant fact at a time."""
    def setUp(self):
        """Load the original model6 capture and choose a test clock one second after real materialization."""
        base = ROOT / 'docs/evidence/final-demo-linux'
        self.row = json.loads((base / 'http-all-machines.json').read_bytes())[0]
        self.model = json.loads((base / 'portable.json').read_bytes())
        self.pin = json.loads((base / 'model-pin.json').read_bytes())
        self.prepared = datetime.fromisoformat(self.row['features']['prepared_wall_utc'])

    def test_native_prediction_matches_independent_arithmetic(self):
        """The retained P-001 score is recomputed from weights rather than accepted from a success flag."""
        actual = DEMO.check_prediction(self.row['features'], self.row['response'], self.model, self.pin, self.prepared + timedelta(seconds=1))
        self.assertAlmostEqual(actual, 0.10318092377929837, places=15)

    def test_expiry_boundary_and_future_preparation_fail(self):
        """Accept just before3600seconds, reject the exact expiry and a clock before preparation."""
        DEMO.check_prediction(self.row['features'], self.row['response'], self.model, self.pin, self.prepared + timedelta(seconds=3599))
        for seconds in [-1, 3600, 3601]:
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                DEMO.check_prediction(self.row['features'], self.row['response'], self.model, self.pin, self.prepared + timedelta(seconds=seconds))

    def test_wrong_model_leakage_and_incorrect_scores_fail(self):
        """Reject a changed version, a label input, unavailable arrival and bad numerical prediction independently."""
        wrong_model = deepcopy(self.row); wrong_model['response']['model']['version'] = '7'
        leaked = deepcopy(self.row); leaked['features']['features']['failure_in_next_1h'] = 1
        leaked['response']['feature_trace'] = deepcopy(leaked['features'])
        future = deepcopy(self.row); future['features']['operating_context']['received_ts'] = '2099-01-01T00:00:00+00:00'
        future['response']['feature_trace'] = deepcopy(future['features'])
        for row in [wrong_model, leaked, future]:
            with self.subTest(kind=row['response']['model']['version']), self.assertRaises(ValueError):
                DEMO.check_prediction(row['features'], row['response'], self.model, self.pin, self.prepared + timedelta(seconds=1))
        for bad in [True, float('nan'), self.row['response']['prediction']['risk_score'] + .01]:
            row = deepcopy(self.row); row['response']['prediction']['risk_score'] = bad
            with self.subTest(score=bad), self.assertRaises(ValueError):
                DEMO.check_prediction(row['features'], row['response'], self.model, self.pin, self.prepared + timedelta(seconds=1))

    def test_real_http_client_and_expiry_receipt_with_local_fixture(self):
        """Exercise all nine HTTP cases against an owned fixture server; never call this a native Feast deployment."""
        trace = deepcopy(self.row['features']); trace['prepared_wall_utc'] = datetime.now(timezone.utc).isoformat()
        response = deepcopy(self.row['response']); response['feature_trace'] = trace
        stopped = deepcopy(trace); stopped['machine_id'] = 'P-006'; stopped['operating_context']['status'] = 'maintenance'
        routes = {'/health/live': (200, {'live': True}), '/health/ready': (200, {'ready': True}),
                  '/v1/machines/P-001/features': (200, trace), '/v1/machines/P-006/features': (200, stopped),
                  '/v1/machines/P-9999/features': (404, {'detail': 'Unknown fixture machine'}),
                  '/v1/machines/invalid/features': (422, {'detail': 'Fixture validation'})}
        class Handler(BaseHTTPRequestHandler):
            """Serve only a tiny labeled HTTP test fixture and discard request logging."""
            def log_message(self, *args):
                """Avoid adding HTTP fixture noise to the test console."""
            def reply(self, code, body):
                """Return a bounded JSON response with explicit length."""
                raw = json.dumps(body).encode(); self.send_response(code)
                self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            def do_GET(self):
                """Read the exact route fixture instead of depending on an external service."""
                self.reply(*routes[self.path])
            def do_POST(self):
                """Consume the request and implement prediction/eligibility responses for two known machines."""
                self.rfile.read(int(self.headers['Content-Length']))
                if self.path == '/v1/machines/P-001:predict': self.reply(200, response)
                else: self.reply(409, {'detail': 'Stopped fixture machine'})
        (ROOT / 'outputs').mkdir(exist_ok=True)
        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server, tempfile.TemporaryDirectory(dir=ROOT / 'outputs') as directory:
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                args = SimpleNamespace(base_url=f'http://127.0.0.1:{server.server_port}', operating='P-001', stopped='P-006',
                    pin=ROOT / 'docs/evidence/final-demo-linux/model-pin.json', portable=ROOT / 'docs/evidence/final-demo-linux/portable.json', output=Path(directory) / 'valid')
                with redirect_stdout(io.StringIO()): self.assertEqual(DEMO.run(args), 0)
                self.assertEqual(json.loads((args.output / 'verification.json').read_bytes())['actual_http_requests'], 9)
                trace['prepared_wall_utc'] = (datetime.now(timezone.utc) - timedelta(seconds=3601)).isoformat()
                args.output = Path(directory) / 'expired'
                with redirect_stdout(io.StringIO()): self.assertEqual(DEMO.run(args), 1)
                self.assertFalse(json.loads((args.output / 'verification.json').read_bytes())['passed'])
            finally:
                server.shutdown(); thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
