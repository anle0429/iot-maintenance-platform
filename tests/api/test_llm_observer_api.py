"""Exercise the observer's actual ASGI proxy, policy admission, native protobuf handling and unchanged response streams."""
from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio,json,unittest
import httpx
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from hypothesis import given,settings,strategies as st
from llm_observability.app import create_app
from llm_observability.ledger import TelemetryLedger
from tests.test_llm_observation_contracts import event,question
from tests.test_llm_private_traces import batch

TOKEN='synthetic-observer-client-'+48*'x'
USAGE={'prompt_tokens':5,'completion_tokens':2,'total_tokens':7}
EXPECTED_TRACE='http://iot-jaeger.iot-observability.svc.cluster.local:4318/v1/traces'
EXPECTED_MODEL='http://127.0.0.1:8080/v1/chat/completions'
EXPECTED_HEALTH='http://127.0.0.1:8080/health'


class Chunks(httpx.AsyncByteStream):
    """Model a real streaming transport, including delayed content following early role-only headers."""
    def __init__(self,chunks,delay=0,error=None):
        """Retain controlled synthetic wire bytes and one optional transport failure."""
        self.chunks=chunks;self.delay=delay;self.error=error
    async def __aiter__(self):
        """Yield each original chunk without aggregating it before the observer receives it."""
        for index,chunk in enumerate(self.chunks):
            if index and self.delay:await asyncio.sleep(self.delay)
            yield chunk
        if self.error:raise self.error
    async def aclose(self):
        """Close this synthetic transport without any external socket or background work."""
        return


class ObserverApiTests(unittest.TestCase):
    """Use independent wire fixtures and actual SQLite/exported spans to verify behavior beyond individual helper calls."""
    def setUp(self):
        """Create a fresh durable store and an injected private backend for each API scenario."""
        self.directory=TemporaryDirectory();self.store=TelemetryLedger(Path(self.directory.name)/'events.sqlite')
        self.logs=[];self.requests=[];self.exporter=InMemorySpanExporter();self.mode='stream';self.status=200;self.delay=0;self.stream_error=None
        self.chunks=[event({'choices':[{'delta':{'role':'assistant'}}]}),event({'choices':[{'delta':{'content':'Synthetic answer'},'finish_reason':'stop'}]}),event({'usage':USAGE}),b'data: [DONE]\n\n']
        async def backend(request):
            """Capture the exact outbound bytes and return only the explicitly selected transport fixture."""
            self.requests.append(request)
            if str(request.url) not in {EXPECTED_TRACE,EXPECTED_MODEL,EXPECTED_HEALTH}:raise AssertionError('Unexpected private destination')
            if self.mode=='transport-error':raise httpx.ConnectError('synthetic unavailable',request=request)
            if str(request.url)==EXPECTED_TRACE:return httpx.Response(self.status,stream=Chunks([b'']))
            if str(request.url)==EXPECTED_HEALTH:return httpx.Response(self.status,json={'ready':self.status==200})
            if self.mode=='json':
                raw=json.dumps({'choices':[{'message':{'content':'Synthetic answer'},'finish_reason':'stop'}],'usage':USAGE}).encode()
                return httpx.Response(self.status,headers={'Content-Type':'application/json'},stream=Chunks([raw]))
            return httpx.Response(self.status,headers={'Content-Type':'text/event-stream','X-Iot-Tool-Control':'serial-close-v1'},stream=Chunks(self.chunks,self.delay,self.stream_error))
        self.upstream=httpx.AsyncClient(transport=httpx.MockTransport(backend),trust_env=False)
        self.app=create_app(ledger=self.store,client=self.upstream,token=TOKEN,span_exporter=self.exporter,emit=self.logs.append)
        self.api=TestClient(self.app,raise_server_exceptions=False);self.api.__enter__()
    def tearDown(self):
        """Flush telemetry and close only the test-owned resources after inspecting their independent readbacks."""
        self.api.__exit__(None,None,None);asyncio.run(self.upstream.aclose());self.store.close();self.directory.cleanup()
    def post(self,payload=None,**kwargs):
        """Send a valid authenticated model request unless a test explicitly replaces its transport fields."""
        return self.api.post('/v1/chat/completions',json=question('Explain a pump feature timestamp.') if payload is None else payload,
                             headers={'Authorization':'Bearer '+TOKEN},**kwargs)

    def test_stream_bytes_request_body_usage_and_timing_are_preserved(self):
        """Keep whitespace and SSE bytes exact while measuring delayed content rather than early headers."""
        self.delay=.02;payload=question('Explain a pump feature timestamp.');raw=(' \n'+json.dumps(payload,indent=2)+'\n').encode()
        response=self.api.post('/v1/chat/completions',content=raw,headers={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json','X-Private':'do-not-forward'})
        self.assertEqual(response.status_code,200);self.assertEqual(response.content,b''.join(self.chunks));self.assertEqual(self.requests[0].content,raw)
        self.assertEqual(self.requests[0].headers['accept-encoding'],'identity');self.assertNotIn('x-private',self.requests[0].headers)
        row=self.store.rows()[0];self.assertEqual(row['facts']['usage'],USAGE);self.assertGreaterEqual(row['facts']['ttft_seconds'],.02)
        self.assertEqual(row['facts']['result'],'ok');self.assertEqual(response.headers['X-Iot-Request-Id'],row['event_id'])
        self.assertEqual(response.headers['X-Iot-Tool-Control'],'serial-close-v1')
        self.app.state.provider.force_flush();spans=self.exporter.get_finished_spans();self.assertEqual(len(spans),1)
        self.assertEqual(spans[0].attributes['gen_ai.usage.input_tokens'],5);self.assertNotIn('Synthetic answer',repr(spans[0].attributes))
        self.assertNotIn(TOKEN,json.dumps(self.logs));self.assertNotIn('Explain a pump',json.dumps(self.logs));self.assertNotIn('Synthetic answer',json.dumps(self.logs))

    def test_nonstreaming_json_has_reported_usage_but_no_invented_ttft(self):
        """A JSON response cannot reveal when generation first began, so its TTFT remains absent."""
        self.mode='json';response=self.post(question('Explain timestamps.')|{'stream':False})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['usage'],USAGE)
        self.assertIsNone(self.store.rows()[0]['facts']['ttft_seconds']);self.assertEqual(self.store.rows()[0]['facts']['usage'],USAGE)

    def test_authentication_rejects_before_parsing_or_contacting_the_model(self):
        """Wrong or absent credentials must not run prompt policy, open a backend stream or echo the supplied login."""
        for header in [{},{'Authorization':'Bearer wrong'}]:
            with self.subTest(header=bool(header)):
                response=self.api.post('/v1/chat/completions',content=b'not JSON',headers=header)
                self.assertEqual(response.status_code,401);self.assertNotIn('wrong',response.text)
        self.assertEqual(self.requests,[]);self.assertEqual(len(self.store.rows()),2)

    def test_safety_and_pii_blocks_happen_before_any_gpu_request(self):
        """Reconcile each declared guard category with an actual rejection and a durable safe fact."""
        for text,category in [('Contact fictional@example.com','pii_email'),('phone: +1 202 555 0199','pii_phone'),
                              ('api_key=synthetic_123456789_secret','secret_literal'),('Stop the pump now','hardware_command')]:
            with self.subTest(category=category):self.assertEqual(self.post(question(text)).status_code,422)
        self.assertEqual(self.requests,[]);self.assertEqual({r['facts']['policy_categories'][0] for r in self.store.rows()},
                                                         {'pii_email','pii_phone','secret_literal','hardware_command'})
        self.assertNotIn('fictional@example.com',json.dumps(self.logs))

    def test_invalid_json_query_and_oversized_bodies_fail_before_upstream(self):
        """Exercise request partitions without relying on internal parser implementation details."""
        headers={'Authorization':'Bearer '+TOKEN}
        self.assertEqual(self.api.post('/v1/chat/completions',content=b'bad',headers=headers).status_code,400)
        self.assertEqual(self.api.post('/v1/chat/completions?destination=foreign',json=question('x'),headers=headers).status_code,400)
        self.assertEqual(self.api.post('/v1/chat/completions',content=b'x'*(1024**2+1),headers=headers).status_code,413)
        self.assertEqual(self.post(question('x')|{'model':'foreign-model'}).status_code,400);self.assertEqual(self.requests,[])
        self.assertEqual(self.api.delete('/v1/chat/completions').status_code,405)

    def test_upstream_http_error_keeps_status_and_original_body(self):
        """A backend rejection cannot become a successful-looking replacement model answer."""
        self.status=429;self.chunks=[b'original upstream throttle'];response=self.post()
        self.assertEqual(response.status_code,429);self.assertEqual(response.content,b'original upstream throttle')
        self.assertEqual(self.store.rows()[0]['facts']['result'],'upstream_error');self.assertIsNone(self.store.rows()[0]['facts']['usage'])

    def test_transport_error_is_safe_and_visible(self):
        """Record a failed backend attempt with no token fabrication or exception details in the public response."""
        self.mode='transport-error';response=self.post();self.assertEqual(response.status_code,502)
        self.assertNotIn('synthetic unavailable',response.text);self.assertEqual(self.store.rows()[0]['facts']['result'],'transport_error')

    def test_midstream_transport_failure_keeps_its_failed_measurement_and_ends_the_span(self):
        """A broken body cannot become complete token accounting or an unclosed successful trace."""
        self.chunks=[self.chunks[1]];self.stream_error=httpx.ReadError('synthetic stream failure');response=self.post()
        fact=self.store.rows()[0]['facts'];self.assertEqual(fact['result'],'transport_error');self.assertIsNone(fact['usage'])
        self.assertIsNotNone(fact['ttft_seconds']);self.assertNotIn('synthetic stream failure',response.text)
        self.app.state.provider.force_flush();spans=self.exporter.get_finished_spans();self.assertEqual(len(spans),1)
        self.assertEqual(spans[0].status.status_code.name,'ERROR')

    def test_truncated_invalid_or_usage_less_streams_remain_explicit_measurement_failures(self):
        """Preserve received bytes but mark absent final usage or invalid protocol as incomplete observations."""
        for chunks in [[self.chunks[1]], [b'data: invalid\n\n'],[self.chunks[1],b'data: [DONE]\n\n']]:
            with self.subTest(chunks=len(chunks)):
                self.chunks=chunks;response=self.post();self.assertEqual(response.content,b''.join(chunks))
        self.assertTrue(all(r['facts']['result']=='invalid_response' for r in self.store.rows()))

    def test_zero_output_keeps_reported_counts_without_inventing_first_token_timing(self):
        """Preserve the provider's actual zero-output accounting even when no generated fragment was observed."""
        usage={'prompt_tokens':5,'completion_tokens':0,'total_tokens':5}
        self.chunks=[event({'choices':[{'delta':{},'finish_reason':'stop'}]}),event({'usage':usage}),b'data: [DONE]\n\n']
        self.post();fact=self.store.rows()[0]['facts'];self.assertEqual(fact['usage'],usage);self.assertIsNone(fact['ttft_seconds'])
        self.assertEqual(fact['result'],'invalid_response')

    def test_metrics_reconcile_with_durable_facts_and_exclude_payload_text(self):
        """Expose the measured request and token values, plus the actual fixed-route HTTP count."""
        self.post();response=self.api.get('/metrics');self.assertEqual(response.status_code,200)
        self.assertIn('iot_llm_reported_tokens_total{agent="direct",model="iot-qwen2.5-7b",type="total"} 7',response.text)
        self.assertIn('iot_llm_observer_http_requests_total{route="model",status="200"} 1',response.text)
        self.assertNotIn(TOKEN,response.text);self.assertNotIn('Synthetic answer',response.text)

    def test_readiness_distinguishes_full_storage_and_backend_failure(self):
        """Keep liveness separate and refuse additional generation before spending GPU work when full."""
        self.assertEqual(self.api.get('/health/live').json(),{'live':True});self.assertEqual(self.api.get('/health/ready').status_code,200)
        self.status=503;self.assertEqual(self.api.get('/health/ready').status_code,503)
        self.status=200;self.store.limit=1;self.post();requests=len(self.requests)
        self.assertEqual(self.api.get('/health/ready').status_code,503);self.assertEqual(self.post().status_code,503);self.assertEqual(len(self.requests),requests)

    def test_private_trace_projection_replay_and_backend_export_are_real(self):
        """Forward a sanitized actual protobuf message and count its original span once across duplicate deliveries."""
        native,_=batch();raw=native.SerializeToString();headers={'Content-Type':'application/x-protobuf'}
        for _ in range(2):self.assertEqual(self.api.post('/v1/traces',content=raw,headers=headers).status_code,200)
        self.assertEqual(len(self.store.rows()),1);self.assertEqual([r['new_spans'] for r in self.logs],[1,0])
        self.assertEqual(str(self.requests[0].url),EXPECTED_TRACE);self.assertNotIn(b'SECRET',self.requests[0].content)
        self.assertEqual(self.requests[0].content,self.requests[1].content)

    def test_bad_trace_payloads_and_storage_outages_do_not_look_successful(self):
        """Keep private ingestion protocol errors and Jaeger failures separate from accepted trace exports."""
        self.assertEqual(self.api.post('/v1/traces',content=b'wrong').status_code,415)
        headers={'Content-Type':'application/x-protobuf'}
        self.assertEqual(self.api.post('/v1/traces',content=b'wrong',headers=headers).status_code,400)
        self.assertEqual(self.api.post('/v1/traces',content=b'x'*(2*1024**2+1),headers=headers).status_code,413)
        native,_=batch();self.status=503
        self.assertEqual(self.api.post('/v1/traces',content=native.SerializeToString(),headers=headers).status_code,503)
        self.assertEqual(len(self.store.rows()),1)

    def test_trace_transport_failure_and_backend_readiness_failure_remain_visible(self):
        """Retain received trace facts for safe replay while reporting failed downstream delivery."""
        self.mode='transport-error';native,_=batch()
        response=self.api.post('/v1/traces',content=native.SerializeToString(),headers={'Content-Type':'application/x-protobuf'})
        self.assertEqual(response.status_code,503);self.assertEqual(len(self.store.rows()),1)
        self.assertEqual(self.api.get('/health/ready').status_code,503)

    @settings(max_examples=24,deadline=None,derandomize=True)
    @given(st.from_regex(r'[a-z]{1,12}',fullmatch=True))
    def test_generated_email_prompts_are_blocked_without_backend_execution(self,local_part):
        """Apply the PII rule through the real ASGI admission path for a family of synthetic addresses."""
        address=local_part+'@example.invalid';response=self.post(question('Explain the pump and contact '+address))
        self.assertEqual(response.status_code,422);self.assertNotIn(address,response.text);self.assertEqual(self.requests,[])
        record=next(row for row in self.store.rows() if row['event_id']==response.headers['X-Iot-Request-Id'])
        self.assertEqual(record['facts']['policy_categories'],['pii_email'])


if __name__=='__main__':unittest.main()
