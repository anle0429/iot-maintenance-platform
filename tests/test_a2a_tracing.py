"""Check automatic client spans against actual A2A replies without reclassifying HTTP success as task success."""
import json
import unittest
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread
from urllib.request import build_opener,ProxyHandler
from tempfile import TemporaryDirectory
from pathlib import Path
from agents.tracing import A2AObservation


class A2ATracingTests(unittest.TestCase):
    """Preserve context propagation and record only bounded outcome metadata from the live call."""
    def test_standalone_probe_entrypoint_loads_tracing_without_pythonpath(self):
        """The documented file entry point must work outside the repository working directory."""
        env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
        with TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'agents/probe.py'), '--help'],
                                    cwd=directory, env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b'--trace-endpoint', result.stdout)

    def observation(self):
        """Inject a deterministic clock and capture the exact OTLP payload emitted by the normal client."""
        sent=[];clock=iter([1_000_000_000,2_000_000_000])
        return A2AObservation('coordinator',send=sent.append,clock=lambda:next(clock)),sent

    def test_completed_reply_emits_ok_and_propagates_the_actual_client_parent(self):
        """A completed task produces OK on this caller span and preserves task identity without response text."""
        observation,sent=self.observation()
        body=json.dumps({'result':{'id':'a'*32,'status':{'state':'completed'},'artifacts':[{'text':'PRIVATE ANSWER'}]}}).encode()
        report=observation.finish(200,body)
        resource=sent[0]['resourceSpans'][0];span=resource['scopeSpans'][0]['spans'][0]
        self.assertEqual(span['status']['code'],1);self.assertNotIn('parentSpanId',span)
        self.assertEqual(observation.headers['traceparent'],'00-'+span['traceId']+'-'+span['spanId']+'-01')
        self.assertEqual(resource['resource']['attributes'][0]['value']['stringValue'],'iot-a2a-client')
        self.assertEqual(report['task_state'],'completed');self.assertTrue(report['exported'])
        self.assertNotIn('PRIVATE',json.dumps(sent))

    def test_failed_rpc_malformed_and_transport_outcomes_are_errors_even_with_http_200(self):
        """Keep protocol/task/HTTP/transport failures distinguishable and never emit an OK for any of them."""
        cases=[(200,b'{"result":{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","status":{"state":"failed"}}}',None,'failed'),
               (200,b'{"error":{"code":-32603,"message":"PRIVATE ERROR"}}',None,'rpc_error'),
               (200,b'not json',None,'invalid_response'),(503,b'PRIVATE ERROR',None,'http_error'),
               (None,b'', 'TimeoutError','transport_error')]
        for status,body,error,state in cases:
            with self.subTest(state=state):
                observation,sent=self.observation();report=observation.finish(status,body,transport_error=error)
                span=sent[0]['resourceSpans'][0]['scopeSpans'][0]['spans'][0]
                self.assertEqual(span['status']['code'],2);self.assertEqual(report['task_state'],state)
                self.assertNotIn('PRIVATE',json.dumps(sent))

    def test_nonterminal_task_stays_unset_and_unknown_state_is_not_accepted(self):
        """An input-required task is pending rather than success; unrecognized states fail visibly."""
        for state,wanted in [('input-required',0),('working',0),('arbitrary success',2)]:
            observation,sent=self.observation();observation.finish(200,json.dumps({'result':{'status':{'state':state}}}).encode())
            self.assertEqual(sent[0]['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['status']['code'],wanted)

    def test_export_failure_is_visible_without_logging_exception_or_task_text(self):
        """The native reply remains independent from an explicitly failed telemetry export."""
        observation,_=self.observation()
        def fail(_):
            """Simulate an unavailable collector without exposing its address in the receipt."""
            raise OSError('PRIVATE ENDPOINT')
        observation.send=fail;report=observation.finish(200,b'{"result":{"status":{"state":"completed"}}}')
        self.assertFalse(report['exported']);self.assertEqual(report['export_error'],'OSError');self.assertNotIn('PRIVATE',json.dumps(report))

    def test_unknown_role_and_repeated_finish_are_refused(self):
        """One real call owns one emitted span; user text cannot become a service or role label."""
        with self.assertRaises(ValueError):A2AObservation('PRIVATE USER',send=lambda _:None)
        observation,_=self.observation();observation.finish(503,b'')
        with self.assertRaises(ValueError):observation.finish(503,b'')

    def test_normal_http_client_propagates_context_and_keeps_original_wire_bytes(self):
        """Exercise actual HTTP dispatch/export so the ordinary client, rather than a manual verifier, owns the span."""
        from agents.probe import request
        seen=[];body=b'{"result":{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","status":{"state":"completed"}}}'
        class Handler(BaseHTTPRequestHandler):
            """Serve one native-shaped task and receive the actual metadata-only OTLP call."""
            def log_message(self,*args):
                """Keep the local wire fixture silent without hiding captured requests."""
                pass
            def do_POST(self):
                """Capture the actual A2A or OTLP HTTP request and return the bounded fixture body."""
                raw=self.rfile.read(int(self.headers['Content-Length']))
                seen.append((self.path,dict(self.headers),raw))
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                self.wfile.write(b'{}' if self.path=='/v1/traces' else body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base='http://127.0.0.1:'+str(server.server_port)
            with TemporaryDirectory() as d:
                receipt=Path(d)/'trace.json'
                code,actual,_=request(build_opener(ProxyHandler({})),base+'/api/a2a-sandboxes/iot-agents/iot-coordinator',
                    {'method':'message/send'},trace_endpoint=base+'/v1/traces',trace_receipt=receipt)
                report=json.loads(receipt.read_bytes())
            self.assertEqual((code,actual),(200,body));self.assertEqual(len(seen),2)
            parent=next(v for k,v in seen[0][1].items() if k.lower()=='traceparent')
            span=json.loads(seen[1][2])['resourceSpans'][0]['scopeSpans'][0]['spans'][0]
            self.assertEqual(parent,'00-'+span['traceId']+'-'+span['spanId']+'-01')
            self.assertEqual(report['status_code'],1);self.assertTrue(report['exported'])
        finally:server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main()
