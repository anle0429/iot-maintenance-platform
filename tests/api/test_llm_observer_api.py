"""Exercise the observer's actual ASGI proxy, policy admission, native protobuf handling and unchanged response streams."""
from pathlib import Path
from hashlib import sha256
from tempfile import TemporaryDirectory
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
import asyncio,json,sqlite3,unittest
import httpx
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from hypothesis import given,settings,strategies as st
from prometheus_client.parser import text_string_to_metric_families
from prometheus_client import CollectorRegistry,Counter,Histogram
from llm_observability.app import create_app,bounded_body,HttpMetricsMiddleware,HttpObservation,ModelExchange
import llm_observability.app as observer
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
                return httpx.Response(self.status,headers={'Content-Type':'application/json'},stream=Chunks([getattr(self,'json_response',raw)]))
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
        too_large=self.api.post('/v1/chat/completions',content=b'x'*(1024**2+1),headers=headers)
        self.assertEqual(too_large.status_code,413);self.assertEqual(too_large.json()['error']['code'],'model_request_too_large')
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
        self.assertEqual(self.api.get('/health/ready').json(),{'ready':False});full=self.post()
        self.assertEqual(full.status_code,503);self.assertEqual(full.json(),{'error':{'code':'observation_capacity_reached'}});self.assertEqual(len(self.requests),requests)

    def test_private_trace_projection_replay_and_backend_export_are_real(self):
        """Forward a sanitized actual protobuf message and count its original span once across duplicate deliveries."""
        native,_=batch();raw=native.SerializeToString();headers={'Content-Type':'application/x-protobuf'}
        for _ in range(2):self.assertEqual(self.api.post('/v1/traces',content=raw,headers=headers).status_code,200)
        self.assertEqual(len(self.store.rows()),1);self.assertEqual([r['new_spans'] for r in self.logs],[1,0])
        self.assertEqual(str(self.requests[0].url),EXPECTED_TRACE);self.assertNotIn(b'SECRET',self.requests[0].content)
        self.assertEqual(self.requests[0].content,self.requests[1].content)

    def test_bad_trace_payloads_and_storage_outages_do_not_look_successful(self):
        """Keep private ingestion protocol errors and Jaeger failures separate from accepted trace exports."""
        response=self.api.post('/v1/traces',content=b'wrong');self.assertEqual(response.status_code,415);self.assertEqual(response.json(),{'error':'require_otlp_protobuf'})
        headers={'Content-Type':'application/x-protobuf'}
        response=self.api.post('/v1/traces',content=b'wrong',headers=headers);self.assertEqual(response.status_code,400);self.assertEqual(response.json(),{'error':'invalid_otlp_trace'})
        response=self.api.post('/v1/traces',content=b'x'*(2*1024**2+1),headers=headers);self.assertEqual(response.status_code,413);self.assertEqual(response.json(),{'error':'otlp_request_too_large'})
        native,_=batch();self.status=503
        response=self.api.post('/v1/traces',content=native.SerializeToString(),headers=headers)
        self.assertEqual(response.status_code,503);self.assertEqual(response.json(),{'error':'trace_storage_unavailable'})
        self.assertEqual(len(self.store.rows()),1)

    def test_trace_transport_failure_and_backend_readiness_failure_remain_visible(self):
        """Retain received trace facts for safe replay while reporting failed downstream delivery."""
        self.mode='transport-error';native,_=batch()
        response=self.api.post('/v1/traces',content=native.SerializeToString(),headers={'Content-Type':'application/x-protobuf'})
        self.assertEqual(response.status_code,503);self.assertEqual(len(self.store.rows()),1)
        self.assertEqual(self.api.get('/health/ready').status_code,503)

    def test_http_route_counts_and_lifetimes_reconcile_across_outcome_partitions(self):
        """Observe independent live/ready/unknown/trace/model routes, including rejected inputs and real streamed completion."""
        self.assertEqual(self.api.get('/health/live').json(),{'live':True});self.assertEqual(self.api.get('/health/ready').json(),{'ready':True})
        self.assertEqual(self.api.get('/absent').status_code,404)
        self.assertEqual(self.api.post('/v1/traces',content=b'bad').status_code,415)
        self.assertEqual(self.api.post('/v1/chat/completions',content=b'bad').status_code,401)
        self.assertEqual(self.api.post('/v1/chat/completions',content=b'bad',headers={'Authorization':'Bearer '+TOKEN}).status_code,400)
        self.assertEqual(self.post(question('Contact fictional@example.com')).status_code,422);self.assertEqual(self.post().status_code,200)
        response=self.api.get('/metrics');self.assertEqual(response.status_code,200)
        values={(sample.name,tuple(sorted(sample.labels.items()))):sample.value for family in text_string_to_metric_families(response.text) for sample in family.samples}
        for route,status in [('live','200'),('ready','200'),('other','404'),('traces','415'),('model','401'),('model','400'),('model','422'),('model','200')]:
            self.assertEqual(values[('iot_llm_observer_http_requests_total',tuple(sorted({'route':route,'status':status}.items())))],1)
        for route,count in [('live',1),('ready',1),('other',1),('traces',1),('model',4)]:
            self.assertEqual(values[('iot_llm_observer_http_seconds_count',(('route',route),))],count)
            self.assertGreaterEqual(values[('iot_llm_observer_http_seconds_sum',(('route',route),))],0)
        self.assertEqual(self.store.size(),4)

    def test_rejection_codes_and_safe_messages_are_the_public_api_contract(self):
        """Return precise machine-readable causes and a request correlation ID without executing any backend request."""
        cases=[({},b'bad','/v1/chat/completions',401,'model_authentication_required'),
            ({'Authorization':'Bearer '+TOKEN},b'bad','/v1/chat/completions',400,'invalid_model_request'),
            ({'Authorization':'Bearer '+TOKEN},json.dumps({'model':'iot-qwen2.5-7b','messages':[{'role':{},'content':'x'}]}).encode(),'/v1/chat/completions',400,'invalid_model_request'),
            ({'Authorization':'Bearer '+TOKEN},json.dumps(question('Explain timestamps.')).encode(),'/v1/chat/completions?x=1',400,'unexpected_query'),
            ({'Authorization':'Bearer '+TOKEN},json.dumps(question('Contact fictional@example.com')).encode(),'/v1/chat/completions',422,'prompt_policy_rejected')]
        for headers,wire,path,status,code in cases:
            response=self.api.post(path,content=wire,headers=headers)
            self.assertEqual(response.status_code,status);self.assertEqual(response.json()['error']['code'],code)
            self.assertEqual(response.json()['error']['type'],'invalid_request_error');self.assertEqual(len(response.headers['X-Iot-Request-Id']),32)
            prefix='Remove personal identifiers' if status==422 else 'The model request'
            self.assertTrue(response.json()['error']['message'].startswith(prefix))
        self.assertEqual(self.requests,[]);self.assertEqual(self.store.size(),5)

    def test_real_parent_context_and_safe_logs_agree_with_the_exported_model_span(self):
        """Preserve actual trace parentage but discard baggage, trace-state and unrelated request headers."""
        response=self.api.post('/v1/chat/completions',json=question('Explain timestamps.'),headers={'Authorization':'Bearer '+TOKEN,
            'traceparent':'00-'+32*'a'+'-'+16*'b'+'-01','tracestate':'private=SECRET','baggage':'private=SECRET','X-Private':'SECRET'})
        self.assertEqual(response.status_code,200);self.app.state.provider.force_flush();spans=self.exporter.get_finished_spans();self.assertEqual(len(spans),1)
        span=spans[0];self.assertEqual(span.context.trace_id,int('a'*32,16));self.assertEqual(span.parent.span_id,int('b'*16,16))
        self.assertEqual(span.status.status_code.value,1);self.assertEqual(span.name,'chat iot-qwen2.5-7b');self.assertEqual(span.kind.name,'CLIENT')
        self.assertEqual(dict(span.resource.attributes),{'service.name':'iot_llm_observer','service.namespace':'iot_llm','service.version':'0.1.0'})
        self.assertEqual(span.attributes['http.response.status_code'],200);self.assertEqual(span.attributes['gen_ai.operation.name'],'chat')
        self.assertEqual(span.attributes['gen_ai.request.model'],'iot-qwen2.5-7b');self.assertEqual(span.attributes['gen_ai.agent.name'],'direct')
        self.assertEqual(span.attributes['iot.request_id'],response.headers['X-Iot-Request-Id'])
        self.assertEqual(span.attributes['gen_ai.usage.output_tokens'],2);self.assertGreaterEqual(span.attributes['iot.first_output_seconds'],0)
        self.assertEqual(self.logs[0]['trace_id'],'a'*32);self.assertEqual(self.logs[0]['span_id'],format(span.context.span_id,'016x'))
        self.assertEqual(self.logs[0]['request_id'],response.headers['X-Iot-Request-Id']);self.assertEqual(self.logs[0]['event'],'model_request')
        self.assertEqual(self.requests[0].headers['traceparent'].split('-')[2],format(span.context.span_id,'016x'))
        for key in ['tracestate','baggage','x-private']:self.assertNotIn(key,self.requests[0].headers)

    def test_private_trace_audit_matches_exported_bytes_and_does_not_forward_authentication(self):
        """Reconcile projected output, original counts, response media type and independent hash without trusting an echoed summary."""
        native,_=batch();response=self.api.post('/v1/traces',content=native.SerializeToString(),headers={
            'Content-Type':'application/x-protobuf; charset=binary','Authorization':'Bearer '+TOKEN})
        self.assertEqual(response.status_code,200);self.assertEqual(response.content,b'');self.assertEqual(response.headers['content-type'],'application/x-protobuf')
        self.assertNotIn('authorization',self.requests[0].headers)
        self.assertEqual(self.logs,[{'event':'private_trace_batch','received_spans':1,'new_spans':1,'removed_attributes':3,
            'exported_sha256':sha256(self.requests[0].content).hexdigest()}])

    def test_explicit_invalid_tokens_are_rejected_without_falling_back_to_a_private_file(self):
        """Only an omitted credential requests the mounted file; explicit malformed values fail validation."""
        for token in ['',False,[],{},'x'*31]:
            with self.subTest(token=token),self.assertRaises(ValueError):create_app(ledger=self.store,client=self.upstream,token=token,span_exporter=self.exporter,emit=self.logs.append)

    def test_failed_store_returns_a_safe_unavailable_response_before_gpu_execution(self):
        """Exercise the actual shared SQLite exception handler and exclude internal database details from the API."""
        self.store.close();response=self.post()
        self.assertEqual(response.status_code,503);self.assertEqual(response.json(),{'error':{'code':'observation_unavailable'}})
        self.assertEqual(self.requests,[]);self.assertNotIn('sqlite',response.text);self.assertNotIn('closed',response.text)

    def test_default_log_sink_writes_one_parseable_structured_event(self):
        """Container output must contain one complete JSON event per line rather than a Python object representation."""
        value={'event':'model_request','status':422,'usage':None,'policy_categories':['pii_email']};output=StringIO()
        with redirect_stdout(output):observer.emit_fact(value)
        lines=output.getvalue().splitlines();self.assertEqual(len(lines),1);self.assertEqual(json.loads(lines[0]),value)
        self.assertTrue(output.getvalue().endswith('\n'))

    def test_production_defaults_read_only_the_mounted_key_and_close_owned_adapters(self):
        """Verify the credential mount, fixed exporter, proxy/redirect refusal and cleanup using real temporary adapters."""
        own_store=TelemetryLedger(Path(self.directory.name)/'owned.sqlite')
        own_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'ready':True})),trust_env=False)
        own_exporter=InMemorySpanExporter()
        with patch.object(observer,'TelemetryLedger',return_value=own_store) as storage,patch.object(observer.httpx,'AsyncClient',return_value=own_client) as transport,\
             patch.object(observer,'OTLPSpanExporter',return_value=own_exporter) as exporter,\
             patch.object(Path,'read_text',autospec=True,return_value=TOKEN+'\n') as credential:
            app=create_app()
            storage.assert_called_once_with('/var/lib/iot-llm-observer/telemetry.sqlite')
            credential.assert_called_once_with(Path('/var/run/iot-llm-observer/API_KEY'))
            self.assertFalse(transport.call_args.kwargs['trust_env']);self.assertFalse(transport.call_args.kwargs['follow_redirects'])
            exporter.assert_called_once_with(endpoint=EXPECTED_TRACE,timeout=3)
        with TestClient(app) as api:self.assertEqual(api.get('/health/live').json(),{'live':True})
        self.assertTrue(own_client.is_closed)
        with self.assertRaises(sqlite3.ProgrammingError):own_store.rows()

    def test_json_outcomes_cover_success_boundary_and_malformed_provider_metadata(self):
        """Treat 299 as success and 300 as an upstream outcome; malformed successful JSON must remain visibly invalid."""
        self.mode='json'
        for status,wanted in [(200,'ok'),(299,'ok'),(300,'upstream_error'),(503,'upstream_error')]:
            self.status=status;response=self.post(question('Explain timestamps.')|{'stream':False})
            self.assertEqual(response.status_code,status);identity=response.headers['x-iot-request-id']
            fact=next(r['facts'] for r in self.store.rows() if r['event_id']==identity)
            self.assertEqual(fact['result'],wanted);self.assertEqual(fact['usage'],USAGE if wanted=='ok' else None)
        self.status=200
        values=[b'not json',b'null',b'[]',b'{}',json.dumps({'usage':USAGE,'choices':[]}).encode(),
            json.dumps({'usage':USAGE,'choices':[None]}).encode(),
            json.dumps({'usage':{'prompt_tokens':True,'completion_tokens':2,'total_tokens':3},'choices':[{'finish_reason':'stop'}]}).encode()]
        for raw in values:
            self.json_response=raw;response=self.post(question('Explain timestamps.')|{'stream':False})
            self.assertEqual(response.content,raw);identity=response.headers['x-iot-request-id']
            fact=next(r['facts'] for r in self.store.rows() if r['event_id']==identity)
            self.assertEqual(fact['result'],'invalid_response');self.assertIsNone(fact['ttft_seconds'])
        for reason,wanted in [('length','length'),('tool_calls','tool_calls'),('content_filter','content_filter'),('future_reason','other')]:
            self.json_response=json.dumps({'usage':USAGE,'choices':[{'finish_reason':reason}]}).encode()
            response=self.post(question('Explain timestamps.')|{'stream':False})
            self.assertEqual(next(r['facts']['finish_reason'] for r in self.store.rows() if r['event_id']==response.headers['x-iot-request-id']),wanted)

    def test_omitted_stream_flag_defaults_to_json_without_a_fabricated_ttft(self):
        """The optional OpenAI streaming flag defaults to false and must remain a real boolean in the stored fact."""
        self.mode='json';payload=question('Explain timestamps.');payload.pop('stream');response=self.post(payload)
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['usage'],USAGE)
        self.assertEqual(self.store.size(),1);fact=self.store.rows()[0]['facts'];self.assertIs(fact['streaming'],False)
        self.assertEqual(fact['result'],'ok');self.assertIsNone(fact['ttft_seconds'])

    def test_model_body_boundary_accepts_the_last_byte_and_rejects_the_next(self):
        """Large trusted context may reach the one-MiB body budget; the next byte is rejected before backend execution."""
        self.mode='json';payload=question('Explain timestamps.')|{'stream':False};payload['messages'].insert(0,{'role':'system','content':''})
        size=1048576;payload['messages'][0]['content']='x'*(size-len(json.dumps(payload).encode()));raw=json.dumps(payload).encode();self.assertEqual(len(raw),size)
        headers={'Authorization':'Bearer '+TOKEN};response=self.api.post('/v1/chat/completions',content=raw,headers=headers)
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['usage'],USAGE);self.assertEqual(self.requests[0].content,raw)
        response=self.api.post('/v1/chat/completions',content=raw+b' ',headers=headers)
        self.assertEqual(response.status_code,413);self.assertEqual(response.json()['error']['code'],'model_request_too_large');self.assertEqual(len(self.requests),1)

    def test_malformed_stream_metadata_preserves_wire_bytes_and_a_real_failed_receipt(self):
        """A malformed JSON frame during feed must leave one invalid-response fact rather than an empty successful-looking assertion."""
        self.chunks=[b'data: {invalid}\n\n',event({'choices':[{'delta':{'content':'later'},'finish_reason':'stop'}]}),event({'usage':USAGE}),b'data: [DONE]\n\n']
        response=self.post();self.assertEqual(response.status_code,200);self.assertEqual(response.content,b''.join(self.chunks))
        self.assertEqual(self.store.size(),1);fact=self.store.rows()[0]['facts'];self.assertEqual(fact['result'],'invalid_response')
        self.assertIsNone(fact['usage']);self.assertIsNone(fact['ttft_seconds']);self.app.state.provider.force_flush()
        spans=self.exporter.get_finished_spans();self.assertEqual(len(spans),1);self.assertEqual(spans[0].status.status_code.value,2)

    def test_otlp_body_limit_accepts_a_valid_envelope_with_unknown_padding_at_the_boundary(self):
        """Keep the two-MiB protocol budget while discarding a large unreviewed protobuf field before export."""
        native,_=batch();base=native.SerializeToString();length=2097152-len(base)-5
        number=length;encoded=[]
        while number>127:encoded.append((number&127)|128);number>>=7
        encoded.append(number);raw=base+b'\xfa\x07'+bytes(encoded)+b'x'*length;self.assertEqual(len(raw),2097152)
        response=self.api.post('/v1/traces',content=raw,headers={'Content-Type':'application/x-protobuf'})
        self.assertEqual(response.status_code,200);self.assertEqual(self.store.size(),1);self.assertLess(len(self.requests[0].content),1024)

    def exchange(self,error=None):
        """Create one real request lifecycle with a controlled response transport for cancellation/cleanup assertions."""
        exchange=ModelExchange(self.app.state.runtime,None)
        exchange.fact.update(agent='direct',model='iot-qwen2.5-7b',streaming=True,status=200,result='ok')
        exchange.upstream=httpx.Response(200,stream=Chunks(self.chunks,error=error));exchange.observer=observer.StreamObservation()
        exchange.span=self.app.state.runtime.tracer.start_span('synthetic lifecycle validation')
        return exchange

    def test_cancellation_signal_closes_the_backend_and_ends_the_failed_span(self):
        """A controlled cancellation must preserve chunks already emitted and persist a cancelled outcome exactly once."""
        exchange=self.exchange(asyncio.CancelledError());received=[]
        async def consume():
            """Observe the cancellation raised by the actual lifecycle rather than hiding it in an HTTP adapter."""
            with self.assertRaises(asyncio.CancelledError):
                async for chunk in exchange.stream():received.append(chunk)
        asyncio.run(consume());self.assertEqual(received,self.chunks);self.assertTrue(exchange.upstream.is_closed)
        self.assertEqual(self.store.size(),1);self.assertEqual(self.store.rows()[0]['facts']['result'],'cancelled')
        self.app.state.provider.force_flush();spans=self.exporter.get_finished_spans();self.assertEqual(len(spans),1)
        self.assertEqual(spans[0].status.status_code.value,2)

    def test_failed_final_telemetry_commit_still_closes_the_response_and_ends_its_span(self):
        """A real closed SQLite connection cannot strand a backend response or an unfinished model span."""
        exchange=self.exchange();self.store.close()
        async def consume():
            """Keep the actual failed commit visible to the caller of this lifecycle."""
            with self.assertRaises(sqlite3.ProgrammingError):
                async for _ in exchange.stream():pass
        asyncio.run(consume());self.assertTrue(exchange.upstream.is_closed);self.app.state.provider.force_flush()
        spans=self.exporter.get_finished_spans();self.assertEqual(len(spans),1);self.assertEqual(spans[0].status.status_code.value,2)

    @settings(max_examples=24,deadline=None,derandomize=True)
    @given(st.from_regex(r'[a-z]{1,12}',fullmatch=True))
    def test_generated_email_prompts_are_blocked_without_backend_execution(self,local_part):
        """Apply the PII rule through the real ASGI admission path for a family of synthetic addresses."""
        address=local_part+'@example.invalid';response=self.post(question('Explain the pump and contact '+address))
        self.assertEqual(response.status_code,422);self.assertNotIn(address,response.text);self.assertEqual(self.requests,[])
        record=next(row for row in self.store.rows() if row['event_id']==response.headers['X-Iot-Request-Id'])
        self.assertEqual(record['facts']['policy_categories'],['pii_email'])


class DeclaredBody:
    """Model a bounded ASGI body independently of HTTPX's own earlier header validation."""
    def __init__(self,parts,length=None):
        """Retain controlled wire chunks and count how many were requested by the application."""
        self.parts=parts;self.reads=0;self.headers={} if length is None else {'content-length':length}
    async def stream(self):
        """Yield the declared chunks without adding or normalizing bytes."""
        for part in self.parts:self.reads+=1;yield part


class ObserverTransportTests(unittest.TestCase):
    """Check wire-length partitions and actual ASGI forwarding/metrics, including failures before response headers."""
    def test_body_limits_accept_exact_bytes_and_reject_declared_or_received_overflow(self):
        """The final permitted byte is accepted; an already oversized declaration is refused before reading any chunk."""
        for parts,length in [([b'a',b'b'],'2'),([b'ab'],None),([b'a',b'b'],'00000002'),([], '0')]:
            request=DeclaredBody(parts,length)
            self.assertEqual(asyncio.run(bounded_body(request,2)),b''.join(parts))
        request=DeclaredBody([b'abc'],'3')
        with self.assertRaises(OverflowError):asyncio.run(bounded_body(request,2))
        self.assertEqual(request.reads,0)
        with self.assertRaises(OverflowError):asyncio.run(bounded_body(DeclaredBody([b'a',b'b',b'c']),2))

    def test_invalid_and_mismatched_lengths_cannot_be_silently_normalized(self):
        """Reject signed, spaced, non-ASCII, noninteger and oversized length strings plus both mismatch directions."""
        for length in ['-1','+1',' 1','1 ','1.0','x','','000000002','²']:
            request=DeclaredBody([b'a'],length)
            with self.subTest(length=length),self.assertRaises(ValueError):asyncio.run(bounded_body(request,2))
            self.assertEqual(request.reads,0)
        for length,parts in [('2',[b'a']),('1',[b'ab'])]:
            with self.subTest(length=length),self.assertRaises(ValueError):asyncio.run(bounded_body(DeclaredBody(parts,length),2))

    def test_status_observation_forwards_each_original_asgi_message(self):
        """A body message cannot fabricate response status; the original start message updates it without rewriting bytes."""
        received=[]
        async def send(message):
            """Retain the exact object passed to the downstream ASGI sender."""
            received.append(message)
        observation=HttpObservation(send);body={'type':'http.response.body','body':b'original','more_body':False}
        asyncio.run(observation(body));self.assertEqual(observation.status,500);self.assertIs(received[0],body)
        start={'type':'http.response.start','status':429,'headers':[(b'x-demo',b'original')]}
        asyncio.run(observation(start));self.assertEqual(observation.status,429);self.assertIs(received[1],start)

    def collectors(self):
        """Create independent real Prometheus collectors for the public route/status and duration contracts."""
        registry=CollectorRegistry();calls=Counter('validation_http_requests_total','Validation calls',['route','status'],registry=registry)
        durations=Histogram('validation_http_seconds','Validation duration',['route'],registry=registry)
        return registry,calls,durations

    def test_non_http_scopes_reach_the_application_without_http_samples(self):
        """Lifespan and websocket work must not be mislabeled as HTTP generation requests."""
        registry,calls,durations=self.collectors();scopes=[]
        async def application(scope,receive,send):
            """Retain the original non-HTTP scope without sending an HTTP response."""
            scopes.append(scope)
        middleware=HttpMetricsMiddleware(application,calls,durations)
        for scope in [{'type':'lifespan'},{'type':'websocket','path':'/socket'}]:
            asyncio.run(middleware(scope,None,None));self.assertIs(scopes[-1],scope)
        self.assertIsNone(registry.get_sample_value('validation_http_requests_total',{'route':'other','status':'500'}))

    def test_failure_before_headers_still_records_a_failed_http_exchange(self):
        """A raised application error cannot disappear from request counts or lifetime samples."""
        registry,calls,durations=self.collectors()
        async def application(scope,receive,send):
            """Fail before the server can emit any response headers."""
            raise RuntimeError('synthetic pre-header failure')
        middleware=HttpMetricsMiddleware(application,calls,durations)
        with self.assertRaisesRegex(RuntimeError,'pre-header'):asyncio.run(middleware({'type':'http','path':'/v1/chat/completions'},None,None))
        self.assertEqual(registry.get_sample_value('validation_http_requests_total',{'route':'model','status':'500'}),1)
        self.assertEqual(registry.get_sample_value('validation_http_seconds_count',{'route':'model'}),1)


if __name__=='__main__':unittest.main()
