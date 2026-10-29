"""Observe unchanged model response bytes and private native traces behind the existing llm-d router."""
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


class HttpMetricsMiddleware:
    """Measure bounded HTTP routes without buffering response bodies or logging headers, URLs or request content."""
    def __init__(self,app,calls,durations):
        """Keep explicitly scoped collectors rather than global registries or automatic payload instrumentation."""
        self.app=app;self.calls=calls;self.durations=durations

    async def __call__(self,scope,receive,send):
        """Preserve every ASGI message while observing status and total stream lifetime independently."""
        if scope['type']!='http':return await self.app(scope,receive,send)
        route={'/v1/chat/completions':'model','/v1/traces':'traces','/metrics':'metrics','/health/live':'live','/health/ready':'ready'}.get(scope.get('path'),'other')
        started=perf_counter();status=500
        async def observe(message):
            """Read the native response status without changing headers, chunks or terminal messages."""
            nonlocal status
            if message['type']=='http.response.start':status=message['status']
            await send(message)
        try:await self.app(scope,receive,observe)
        finally:
            self.calls.labels(route,str(status)).inc();self.durations.labels(route).observe(perf_counter()-started)


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


def create_app(ledger=None,client=None,token=None,span_exporter=None,emit=None):
    """Inject test adapters or use one fixed private backend, mounted credential and durable owned database."""
    owned_store=ledger is None;owned_client=client is None
    ledger=ledger or TelemetryLedger('/var/lib/iot-llm-observer/telemetry.sqlite')
    token=token or Path('/var/run/iot-llm-observer/API_KEY').read_text().strip()
    if not isinstance(token,str) or len(token)<32:raise ValueError('Require the retained strong model-client credential')
    expected=('Bearer '+token).encode();registry=CollectorRegistry()
    calls=Counter('iot_llm_observer_http_requests_total','Observer HTTP responses by fixed route and actual status.',['route','status'],registry=registry)
    durations=Histogram('iot_llm_observer_http_seconds','Observer HTTP response lifetime, including streamed bodies.',['route'],registry=registry)
    provider=TracerProvider(resource=Resource({'service.name':'iot_llm_observer','service.namespace':'iot_llm','service.version':'0.1.0'}))
    provider.add_span_processor(BatchSpanProcessor(span_exporter or OTLPSpanExporter(endpoint=TRACE_ENDPOINT,timeout=3),
                                                max_queue_size=256,max_export_batch_size=64,schedule_delay_millis=500))
    tracer=provider.get_tracer('iot.llm.observer','0.1.0');trace_processor=PrivateTraceProcessor()
    client=client or httpx.AsyncClient(timeout=httpx.Timeout(180,connect=5,write=15,pool=5),trust_env=False,follow_redirects=False)
    emit=emit or (lambda value:print(json.dumps(value,separators=(',',':')),flush=True))
    @asynccontextmanager
    async def lifespan(app):
        """Drain the bounded telemetry exporter and close only resources owned by this application."""
        try:yield
        finally:
            await asyncio.to_thread(provider.force_flush,3000);await asyncio.to_thread(provider.shutdown)
            if owned_client:await client.aclose()
            if owned_store:ledger.close()
    app=FastAPI(title='Private IoT LLM observation',version='0.1.0',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.ledger=ledger;app.state.provider=provider
    app.add_middleware(HttpMetricsMiddleware,calls=calls,durations=durations)
    @app.exception_handler(ValueError)
    @app.exception_handler(sqlite3.Error)
    async def unavailable(request,error):
        """Keep storage/observation failures visible without returning database paths or exception contents."""
        return JSONResponse({'error':{'code':'observation_unavailable'}},503)
    @app.get('/health/live')
    async def live():
        """Report this process independently of downstream model readiness."""
        return {'live':True}
    @app.get('/health/ready')
    async def ready():
        """Refuse new model work when the durable budget is full or the actual backend is not ready."""
        if ledger.size()>=ledger.limit:return JSONResponse({'ready':False},503)
        try:response=await client.get(UPSTREAM+'/health')
        except httpx.HTTPError:return JSONResponse({'ready':False},503)
        return JSONResponse({'ready':response.status_code==200},200 if response.status_code==200 else 503)
    @app.get('/metrics')
    async def metrics():
        """Expose durable deduplicated model/agent/tool facts plus ordinary process-lifetime HTTP counters."""
        return Response(render_metrics(ledger.rows())+generate_latest(registry),media_type=CONTENT_TYPE_LATEST)
    @app.post('/v1/traces')
    async def traces(request:Request):
        """Accept only bounded private OTLP, remove unapproved data and preserve exact native span identities on replay."""
        if request.headers.get('content-type','').split(';')[0].strip()!='application/x-protobuf':return JSONResponse({'error':'require_otlp_protobuf'},415)
        try:projected=trace_processor.project(await bounded_body(request,2*1024**2))
        except OverflowError:return JSONResponse({'error':'otlp_request_too_large'},413)
        except ValueError:return JSONResponse({'error':'invalid_otlp_trace'},400)
        inserted=0
        for identity,fact in projected['spans']:inserted+=ledger.put('span',identity,fact)
        try:response=await client.post(TRACE_ENDPOINT,content=projected['payload'],headers={'Content-Type':'application/x-protobuf'})
        except httpx.HTTPError:return JSONResponse({'error':'trace_storage_unavailable'},503)
        if response.status_code!=200:return JSONResponse({'error':'trace_storage_unavailable'},503)
        emit({'event':'private_trace_batch','received_spans':projected['received_spans'],'new_spans':inserted,
              'removed_attributes':projected['removed_attributes'],'exported_sha256':sha256(projected['payload']).hexdigest()})
        return Response(b'',media_type='application/x-protobuf')
    @app.post('/v1/chat/completions')
    async def model(request:Request):
        """Check the existing login and text policy, then forward original model bytes while measuring real output fragments."""
        started=perf_counter();identity=uuid4().hex;span=None
        fact={'agent':'unknown','model':'unknown','duration_seconds':0,'ttft_seconds':None,'usage':None,'status':400,'result':'invalid_request',
              'streaming':False,'finish_reason':None,'policy_categories':[]}
        def record():
            """Commit approved facts once and emit a structured event without any input, output or authentication text."""
            fact['duration_seconds']=perf_counter()-started;ledger.put('request',identity,fact)
            context=span.get_span_context() if span is not None else None
            emit({'event':'model_request','request_id':identity,'trace_id':format(context.trace_id,'032x') if context else None,
                  'span_id':format(context.span_id,'016x') if context else None,**fact})
        def reject(status,code):
            """Reject before model execution and keep an auditable safe status/category outcome."""
            fact['status']=status;record();headers={'X-Iot-Request-Id':identity}
            if status==401:headers['WWW-Authenticate']='Bearer'
            message=('Remove personal identifiers or credentials. This assistant reads pump data and cannot operate equipment.'
                     if code=='prompt_policy_rejected' else 'The model request could not be accepted. Check its credentials, format and service readiness.')
            return JSONResponse({'error':{'code':code,'message':message,'type':'invalid_request_error'}},status,headers=headers)
        if not compare_digest(request.headers.get('authorization','').encode(),expected):return reject(401,'model_authentication_required')
        if ledger.size()>=ledger.limit:return JSONResponse({'error':{'code':'observation_capacity_reached'}},503)
        if request.url.query:return reject(400,'unexpected_query')
        try:
            raw=await bounded_body(request,1024**2);payload=json.loads(raw);categories=PromptPolicy().inspect(payload)
        except OverflowError:return reject(413,'model_request_too_large')
        except (ValueError,RecursionError):return reject(400,'invalid_model_request')
        fact.update(agent=agent_role(payload),model=MODEL,streaming=payload.get('stream',False),policy_categories=categories)
        if categories:
            fact['result']='policy_rejected';return reject(422,'prompt_policy_rejected')
        context=TraceContextTextMapPropagator().extract({'traceparent':request.headers.get('traceparent','')})
        span=tracer.start_span('chat '+MODEL,context=context,kind=SpanKind.CLIENT,
            attributes={'gen_ai.operation.name':'chat','gen_ai.request.model':MODEL,'gen_ai.agent.name':fact['agent'],'iot.request_id':identity})
        headers={'Content-Type':'application/json','Accept':request.headers.get('accept','application/json'),'Accept-Encoding':'identity','Authorization':request.headers['authorization']}
        TraceContextTextMapPropagator().inject(headers,context=set_span_in_context(span))
        try:upstream=await client.send(client.build_request('POST',UPSTREAM+'/v1/chat/completions',content=raw,headers=headers),stream=True)
        except httpx.HTTPError:
            span.set_status(Status(StatusCode.ERROR));span.end();fact['result']='transport_error';return reject(502,'model_backend_unavailable')
        fact['status']=upstream.status_code;fact['result']='ok' if 200<=upstream.status_code<300 else 'upstream_error'
        observer=StreamObservation() if fact['streaming'] and fact['result']=='ok' else None
        async def stream():
            """Yield unchanged chunks; protocol failures remain explicit measurements rather than fabricated text or token totals."""
            collected=bytearray();observing=True
            try:
                async for chunk in upstream.aiter_raw():
                    if observer is not None and observing:
                        try:observer.feed(chunk,perf_counter()-started)
                        except ValueError:observing=False;fact['result']='invalid_response'
                    elif observer is None and len(collected)<=4*1024**2:collected.extend(chunk)
                    yield chunk
                if observer is not None:
                    fact['ttft_seconds']=observer.first_output_seconds;fact['finish_reason']=observer.finish_reason
                    if observing:
                        try:
                            observation=observer.finish(perf_counter()-started)
                            fact['usage']=observation['usage']
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
            finally:
                try:
                    await upstream.aclose()
                    if observer is not None:fact['ttft_seconds']=observer.first_output_seconds;fact['finish_reason']=observer.finish_reason
                    record();span.set_attribute('http.response.status_code',fact['status'])
                    if fact['usage']:
                        span.set_attribute('gen_ai.usage.input_tokens',fact['usage']['prompt_tokens']);span.set_attribute('gen_ai.usage.output_tokens',fact['usage']['completion_tokens'])
                    if fact['ttft_seconds'] is not None:span.set_attribute('iot.first_output_seconds',fact['ttft_seconds'])
                    if fact['result']!='ok':span.set_status(Status(StatusCode.ERROR))
                    else:span.set_status(Status(StatusCode.OK))
                except Exception:
                    span.set_status(Status(StatusCode.ERROR));raise
                finally:span.end()
        response_headers={'X-Iot-Request-Id':identity}
        for name in ['content-type','content-encoding','x-iot-tool-control']:
            if name in upstream.headers:response_headers[name]=upstream.headers[name]
        return StreamingResponse(stream(),status_code=upstream.status_code,headers=response_headers)
    return app
