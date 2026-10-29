"""Observe unchanged model bytes and private native traces through explicit service and request lifecycles."""
from contextlib import asynccontextmanager
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from time import perf_counter
from uuid import uuid4
import asyncio,json,sqlite3
import httpx
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse,Response,StreamingResponse
from prometheus_client import CollectorRegistry,Counter,Histogram,generate_latest,CONTENT_TYPE_LATEST
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind,Status,StatusCode,set_span_in_context
from llm_observability.contracts import MODEL,PromptPolicy,StreamObservation,agent_role,token_usage
from llm_observability.ledger import TelemetryLedger
from llm_observability.metrics import render_metrics
from llm_observability.private_traces import PrivateTraceProcessor

UPSTREAM='http://127.0.0.1:8080'
TRACE_ENDPOINT='http://iot-jaeger.iot-observability.svc.cluster.local:4318/v1/traces'


class HttpObservation:
    """Keep response status local to one concurrent ASGI exchange while forwarding every original message."""
    def __init__(self,send):
        """Start at an explicit failure status in case no response headers can be sent."""
        self.send=send;self.status=500

    async def __call__(self,message):
        """Read only the response-start status and leave the message unchanged."""
        if message['type']=='http.response.start':self.status=message['status']
        await self.send(message)


class HttpMetricsMiddleware:
    """Measure bounded routes without buffering bodies or logging headers, URLs or request content."""
    def __init__(self,app,calls,durations):
        """Keep explicitly scoped collectors rather than global registries or payload instrumentation."""
        self.app=app;self.calls=calls;self.durations=durations

    async def __call__(self,scope,receive,send):
        """Preserve every ASGI message while measuring status and total stream lifetime independently."""
        if scope['type']!='http':return await self.app(scope,receive,send)
        route={'/v1/chat/completions':'model','/v1/traces':'traces','/metrics':'metrics','/health/live':'live','/health/ready':'ready'}.get(scope.get('path'),'other')
        started=perf_counter();observation=HttpObservation(send)
        try:await self.app(scope,receive,observation)
        finally:
            self.calls.labels(route,str(observation.status)).inc();self.durations.labels(route).observe(perf_counter()-started)


async def bounded_body(request,limit):
    """Bound the actual received body and reject inconsistent declared lengths before forwarding it."""
    declared=request.headers.get('content-length');size=None
    if declared is not None:
        if not declared.isascii() or not declared.isdigit() or len(declared)>8:raise ValueError('Invalid request length')
        size=int(declared)
        if size>limit:raise OverflowError('Request body limit exceeded')
    parts=[];total=0
    async for part in request.stream():
        total+=len(part)
        if total>limit:raise OverflowError('Request body limit exceeded')
        parts.append(part)
    if size is not None and total!=size:raise ValueError('Request length mismatch')
    return b''.join(parts)


def emit_fact(value):
    """Write only the already-approved structured telemetry event to the container output."""
    print(json.dumps(value,separators=(',',':')),flush=True)


class ObserverRuntime:
    """Own shared adapters and lifecycle; individual model requests keep their state in separate exchanges."""
    def __init__(self,ledger,client,token,span_exporter,emit):
        """Inject test adapters or open the fixed private backend, mounted credential and owned database."""
        self.owned_store=ledger is None;self.owned_client=client is None
        self.ledger=ledger or TelemetryLedger('/var/lib/iot-llm-observer/telemetry.sqlite')
        if token is None:token=Path('/var/run/iot-llm-observer/API_KEY').read_text().strip()
        if not isinstance(token,str) or len(token)<32:raise ValueError('Require the retained strong model-client credential')
        self.expected=('Bearer '+token).encode();self.registry=CollectorRegistry()
        self.calls=Counter('iot_llm_observer_http_requests_total','Observer HTTP responses by fixed route and actual status.',['route','status'],registry=self.registry)
        self.durations=Histogram('iot_llm_observer_http_seconds','Observer HTTP response lifetime, including streamed bodies.',['route'],registry=self.registry)
        self.provider=TracerProvider(resource=Resource({'service.name':'iot_llm_observer','service.namespace':'iot_llm','service.version':'0.1.0'}))
        self.provider.add_span_processor(BatchSpanProcessor(span_exporter or OTLPSpanExporter(endpoint=TRACE_ENDPOINT,timeout=3),
            max_queue_size=256,max_export_batch_size=64,schedule_delay_millis=500))
        self.tracer=self.provider.get_tracer('iot.llm.observer','0.1.0');self.trace_processor=PrivateTraceProcessor()
        self.client=client or httpx.AsyncClient(timeout=httpx.Timeout(180,connect=5,write=15,pool=5),trust_env=False,follow_redirects=False)
        self.emit=emit or emit_fact

    async def shutdown(self):
        """Drain the bounded exporter and close only the store/client owned by this service."""
        await asyncio.to_thread(self.provider.force_flush,3000);await asyncio.to_thread(self.provider.shutdown)
        if self.owned_client:await self.client.aclose()
        if self.owned_store:self.ledger.close()

    async def unavailable(self,request:Request,error):
        """Keep storage failures visible without returning database paths or exception contents."""
        return JSONResponse({'error':{'code':'observation_unavailable'}},503)

    async def live(self):
        """Keep process liveness independent of the GPU and observation capacity."""
        return {'live':True}

    async def ready(self):
        """Admit traffic only while the store has capacity and the fixed model backend is healthy."""
        if self.ledger.size()>=self.ledger.limit:return JSONResponse({'ready':False},503)
        try:response=await self.client.get(UPSTREAM+'/health')
        except httpx.HTTPError:return JSONResponse({'ready':False},503)
        return JSONResponse({'ready':response.status_code==200},200 if response.status_code==200 else 503)

    async def metrics(self):
        """Expose deduplicated domain facts alongside ordinary process-lifetime HTTP counters."""
        return Response(render_metrics(self.ledger.rows())+generate_latest(self.registry),media_type=CONTENT_TYPE_LATEST)

    async def traces(self,request:Request):
        """Project bounded private OTLP before storage and preserve exact identities when a batch is replayed."""
        if request.headers.get('content-type','').split(';')[0].strip()!='application/x-protobuf':return JSONResponse({'error':'require_otlp_protobuf'},415)
        try:projected=self.trace_processor.project(await bounded_body(request,2*1024**2))
        except OverflowError:return JSONResponse({'error':'otlp_request_too_large'},413)
        except ValueError:return JSONResponse({'error':'invalid_otlp_trace'},400)
        inserted=0
        for identity,fact in projected['spans']:inserted+=self.ledger.put('span',identity,fact)
        try:response=await self.client.post(TRACE_ENDPOINT,content=projected['payload'],headers={'Content-Type':'application/x-protobuf'})
        except httpx.HTTPError:return JSONResponse({'error':'trace_storage_unavailable'},503)
        if response.status_code!=200:return JSONResponse({'error':'trace_storage_unavailable'},503)
        self.emit({'event':'private_trace_batch','received_spans':projected['received_spans'],'new_spans':inserted,
            'removed_attributes':projected['removed_attributes'],'exported_sha256':sha256(projected['payload']).hexdigest()})
        return Response(b'',media_type='application/x-protobuf')

    async def model(self,request:Request):
        """Allocate isolated request state and execute the model admission/forwarding lifecycle."""
        return await ModelExchange(self,request).execute()


class ModelExchange:
    """Own one request's identity, timings, safe facts and backend stream; never share them across requests."""
    def __init__(self,runtime,request):
        """Initialize a bounded default invalid outcome before parsing any untrusted bytes."""
        self.runtime=runtime;self.request=request;self.started=perf_counter();self.identity=uuid4().hex;self.span=None
        self.upstream=None;self.observer=None
        self.fact={'agent':'unknown','model':'unknown','duration_seconds':0,'ttft_seconds':None,'usage':None,'status':400,'result':'invalid_request',
            'streaming':False,'finish_reason':None,'policy_categories':[]}

    def record(self):
        """Commit approved facts once and log their real correlation IDs without any input/output/authentication text."""
        self.fact['duration_seconds']=perf_counter()-self.started;self.runtime.ledger.put('request',self.identity,self.fact)
        context=self.span.get_span_context() if self.span is not None else None
        self.runtime.emit({'event':'model_request','request_id':self.identity,'trace_id':format(context.trace_id,'032x') if context else None,
            'span_id':format(context.span_id,'016x') if context else None,**self.fact})

    def reject(self,status,code):
        """Reject before model execution while keeping an auditable safe status/category outcome."""
        self.fact['status']=status;self.record();headers={'X-Iot-Request-Id':self.identity}
        if status==401:headers['WWW-Authenticate']='Bearer'
        message=('Remove personal identifiers or credentials. This assistant reads pump data and cannot operate equipment.'
            if code=='prompt_policy_rejected' else 'The model request could not be accepted. Check its credentials, format and service readiness.')
        return JSONResponse({'error':{'code':code,'message':message,'type':'invalid_request_error'}},status,headers=headers)

    async def execute(self):
        """Check authentication, capacity and input policy, then open exactly one unchanged backend request."""
        request=self.request;runtime=self.runtime;fact=self.fact
        if not compare_digest(request.headers.get('authorization','').encode(),runtime.expected):return self.reject(401,'model_authentication_required')
        if runtime.ledger.size()>=runtime.ledger.limit:return JSONResponse({'error':{'code':'observation_capacity_reached'}},503)
        if request.url.query:return self.reject(400,'unexpected_query')
        try:
            raw=await bounded_body(request,1024**2);payload=json.loads(raw);categories=PromptPolicy().inspect(payload)
        except OverflowError:return self.reject(413,'model_request_too_large')
        except (ValueError,RecursionError):return self.reject(400,'invalid_model_request')
        fact.update(agent=agent_role(payload),model=MODEL,streaming=payload.get('stream',False),policy_categories=categories)
        if categories:
            fact['result']='policy_rejected';return self.reject(422,'prompt_policy_rejected')
        context=TraceContextTextMapPropagator().extract({'traceparent':request.headers.get('traceparent','')})
        self.span=runtime.tracer.start_span('chat '+MODEL,context=context,kind=SpanKind.CLIENT,
            attributes={'gen_ai.operation.name':'chat','gen_ai.request.model':MODEL,'gen_ai.agent.name':fact['agent'],'iot.request_id':self.identity})
        headers={'Content-Type':'application/json','Accept':request.headers.get('accept','application/json'),'Accept-Encoding':'identity','Authorization':request.headers['authorization']}
        TraceContextTextMapPropagator().inject(headers,context=set_span_in_context(self.span))
        try:self.upstream=await runtime.client.send(runtime.client.build_request('POST',UPSTREAM+'/v1/chat/completions',content=raw,headers=headers),stream=True)
        except httpx.HTTPError:
            self.span.set_status(Status(StatusCode.ERROR));self.span.end();fact['result']='transport_error';return self.reject(502,'model_backend_unavailable')
        fact['status']=self.upstream.status_code;fact['result']='ok' if 200<=self.upstream.status_code<300 else 'upstream_error'
        self.observer=StreamObservation() if fact['streaming'] and fact['result']=='ok' else None
        headers={'X-Iot-Request-Id':self.identity}
        for name in ['content-type','content-encoding','x-iot-tool-control']:
            if name in self.upstream.headers:headers[name]=self.upstream.headers[name]
        return StreamingResponse(self.stream(),status_code=self.upstream.status_code,headers=headers)

    async def stream(self):
        """Forward original chunks; measure genuine output and retain explicit malformed, failed or cancelled outcomes."""
        observer=self.observer;fact=self.fact;collected=bytearray();observing=True
        try:
            async for chunk in self.upstream.aiter_raw():
                if observer is not None and observing:
                    try:observer.feed(chunk,perf_counter()-self.started)
                    except ValueError:observing=False;fact['result']='invalid_response'
                elif observer is None and len(collected)<=4*1024**2:collected.extend(chunk)
                yield chunk
            if observer is not None:
                fact['ttft_seconds']=observer.first_output_seconds;fact['finish_reason']=observer.finish_reason
                if observing:
                    try:
                        observation=observer.finish(perf_counter()-self.started);fact['usage']=observation['usage']
                        if not observation['complete']:fact['result']='invalid_response'
                    except ValueError:fact['result']='invalid_response'
            elif fact['result']=='ok':
                try:
                    value=json.loads(collected);fact['usage']=token_usage(value['usage']);reason=value['choices'][0]['finish_reason']
                    fact['finish_reason']=reason if reason in {'stop','length','tool_calls','content_filter'} else 'other'
                except (ValueError,KeyError,TypeError,IndexError,RecursionError):fact['result']='invalid_response'
        except httpx.HTTPError:
            fact['result']='transport_error';raise
        except asyncio.CancelledError:
            fact['result']='cancelled';raise
        finally:await self.finalize()

    async def finalize(self):
        """Close the backend and always end the real span, including a failed telemetry commit."""
        fact=self.fact
        try:
            await self.upstream.aclose()
            if self.observer is not None:fact['ttft_seconds']=self.observer.first_output_seconds;fact['finish_reason']=self.observer.finish_reason
            self.record();self.span.set_attribute('http.response.status_code',fact['status'])
            if fact['usage']:
                self.span.set_attribute('gen_ai.usage.input_tokens',fact['usage']['prompt_tokens']);self.span.set_attribute('gen_ai.usage.output_tokens',fact['usage']['completion_tokens'])
            if fact['ttft_seconds'] is not None:self.span.set_attribute('iot.first_output_seconds',fact['ttft_seconds'])
            self.span.set_status(Status(StatusCode.OK if fact['result']=='ok' else StatusCode.ERROR))
        except Exception:
            self.span.set_status(Status(StatusCode.ERROR));raise
        finally:self.span.end()


@asynccontextmanager
async def lifecycle(app):
    """Close owned service adapters after all requests have drained."""
    try:yield
    finally:await app.state.runtime.shutdown()


def create_app(ledger=None,client=None,token=None,span_exporter=None,emit=None):
    """Build the same private API from explicit service and per-request objects."""
    runtime=ObserverRuntime(ledger,client,token,span_exporter,emit)
    app=FastAPI(title='Private IoT LLM observation',version='0.1.0',lifespan=lifecycle,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.runtime=runtime;app.state.ledger=runtime.ledger;app.state.provider=runtime.provider
    app.add_middleware(HttpMetricsMiddleware,calls=runtime.calls,durations=runtime.durations)
    app.add_exception_handler(ValueError,runtime.unavailable);app.add_exception_handler(sqlite3.Error,runtime.unavailable)
    for path,endpoint,methods in [('/health/live',runtime.live,['GET']),('/health/ready',runtime.ready,['GET']),('/metrics',runtime.metrics,['GET']),
        ('/v1/traces',runtime.traces,['POST']),('/v1/chat/completions',runtime.model,['POST'])]:
        app.add_api_route(path,endpoint,methods=methods)
    return app
