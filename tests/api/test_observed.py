"""Verify real ASGI metrics, finite labels, trace context across threads/HTTP, payload privacy and startup failure cleanup."""
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread
import asyncio,json,urllib.request
from types import SimpleNamespace
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor,SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Status,StatusCode,SpanKind
from opentelemetry.instrumentation.urllib import URLLibInstrumentor
from services.observed import observe,private_endpoint,PrivateSpanExporter,create_app

ENDPOINT='http://iot-jaeger.iot-observability.svc.cluster.local:4318/v1/traces'


def samples(raw,name):
    """Parse the actual public scrape format, retaining exact label sets and counter values."""
    return [s for family in text_string_to_metric_families(raw) for s in family.samples if s.name==name]


@pytest.fixture
def instrumented():
    """Use a genuine FastAPI and SDK memory exporter instead of manufacturing span/metric records."""
    exporter=InMemorySpanExporter();app=FastAPI()
    @app.get('/v1/machines/{machine_id}')
    async def machine(machine_id:str,fail:bool=False):
        if fail:raise HTTPException(503,'secret database exception')
        return {'machine_id':machine_id,'private_feature':99}
    @app.get('/explode')
    async def explode():raise RuntimeError('private unhandled exception')
    @app.get('/health/live')
    async def live():return {'live':True}
    observe(app,'feature',exporter,SimpleSpanProcessor)
    yield app,exporter


def test_metrics_actual_counts_route_status_and_scrape_exclusion(instrumented):
    app,exporter=instrumented
    with TestClient(app) as client:
        for i in range(30):assert client.get('/v1/machines/P-'+str(i)+'?query=private',headers={'Authorization':'Bearer private-token'}).status_code==200
        assert client.get('/v1/machines/P-1?fail=true').status_code==503
        assert client.get('/unknown-private-machine-id').status_code==404
        for _ in range(3):assert client.get('/health/live').status_code==200
        scrape=client.get('/telemetry/metrics');assert scrape.status_code==200 and scrape.headers['content-type'].startswith('text/plain')
        raw=scrape.text
        counts=samples(raw,'http_server_duration_milliseconds_count')
        got={(s.labels.get('http_target'),s.labels['http_status_code']):s.value for s in counts}
        assert got=={('/v1/machines/{machine_id}','200'):30,('/v1/machines/{machine_id}','503'):1,('','404'):1}
        assert all(s.labels['service_name']=='iot-feature-api' for s in counts)
        assert all(set(s.labels)<={'service_name','http_method','http_status_code','http_target'} for s in counts)
        assert sum(s.value for s in samples(raw,'http_server_duration_milliseconds_count'))==32
        again=client.get('/telemetry/metrics').text
        assert sum(s.value for s in samples(again,'http_server_duration_milliseconds_count'))==32
        assert 'private-token' not in raw and 'private-machine' not in raw and 'P-1' not in raw and 'private_feature' not in raw
        assert len(exporter.get_finished_spans())==32
        assert all(s.attributes.keys()<= {'http.method','http.status_code','http.route'} for s in exporter.get_finished_spans())


def test_unhandled_error_is_counted_and_trace_contains_no_exception_data(instrumented):
    app,exporter=instrumented
    with TestClient(app,raise_server_exceptions=False) as client:
        assert client.get('/explode?password=private-secret').status_code==500
        counts=samples(client.get('/telemetry/metrics').text,'http_server_duration_milliseconds_count')
        assert len(counts)==1 and counts[0].value==1 and counts[0].labels['http_status_code']=='500'
        spans=exporter.get_finished_spans();assert len(spans)==1
        assert spans[0].status.status_code==StatusCode.ERROR and spans[0].status.description is None
        assert spans[0].events==() and spans[0].links==()
        assert 'private' not in spans[0].to_json()


def test_business_suffix_cannot_evade_request_metrics(instrumented):
    app,exporter=instrumented
    with TestClient(app) as client:
        assert client.get('/v1/machines/metrics').status_code==200
        assert len(exporter.get_finished_spans())==1
        assert sum(s.value for s in samples(client.get('/telemetry/metrics').text,'http_server_duration_milliseconds_count'))==1


def test_existing_feature_api_golden_prediction_and_validation_survive_instrumentation():
    from tests.api.test_feature_api import fixture_runtime
    from services.feature_api.app import create_app as feature_app
    runtime=fixture_runtime();exporter=InMemorySpanExporter();app=observe(feature_app(runtime),'feature',exporter,SimpleSpanProcessor)
    expected=runtime.predict('P-001')
    with TestClient(app) as client:
        assert client.post('/v1/machines/P-001:predict').json()==expected
        assert client.post('/v1/machines/invalid:predict').status_code==422
        counts=samples(client.get('/telemetry/metrics').text,'http_server_duration_milliseconds_count')
        assert {(s.labels['http_status_code'],s.value) for s in counts}=={('200',1),('422',1)}
        assert len(exporter.get_finished_spans())==2


def test_exporter_failure_does_not_change_http_result(instrumented,monkeypatch):
    app,exporter=instrumented
    monkeypatch.setattr(exporter,'export',lambda spans:SpanExportResult.FAILURE)
    with TestClient(app) as client:
        assert client.get('/v1/machines/P-001').json()=={'machine_id':'P-001','private_feature':99}


@pytest.mark.parametrize('path',['/health/live','/health/live?a=secret','/health/ready','/telemetry/metrics'])
def test_probes_and_scrapes_do_not_emit_traces(instrumented,path):
    app,exporter=instrumented
    with TestClient(app) as client:
        client.get(path);assert exporter.get_finished_spans()==()


def test_valid_remote_context_and_invalid_context_are_handled(instrumented):
    app,exporter=instrumented
    with TestClient(app) as client:
        client.get('/v1/machines/P-1',headers={'traceparent':'00-12345678901234567890123456789012-1234567890123456-01','baggage':'password=secret'})
        span=exporter.get_finished_spans()[-1]
        assert f'{span.context.trace_id:032x}'=='12345678901234567890123456789012'
        assert f'{span.parent.span_id:016x}'=='1234567890123456' and span.parent.is_remote
        client.get('/v1/machines/P-1',headers={'traceparent':'not-a-valid-trace'})
        second=exporter.get_finished_spans()[-1];assert second.parent is None and second.context.trace_id!=span.context.trace_id
        assert 'password' not in span.to_json() and span.name=='GET /v1/machines/{machine_id}'


def test_real_urllib_call_from_async_thread_preserves_client_parent_and_remote_header(instrumented):
    app,exporter=instrumented;received=[]
    class Receiver(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(dict(self.headers));self.send_response(200);self.end_headers();self.wfile.write(b'ok')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Receiver);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    @app.get('/downstream')
    async def downstream():
        def call():
            with urllib.request.urlopen('http://127.0.0.1:'+str(server.server_port)+'/private?token=secret',timeout=2) as response:return response.read().decode()
        return {'result':await asyncio.to_thread(call)}
    instrumentor=URLLibInstrumentor();instrumentor.instrument(tracer_provider=app.state.observability['traces'])
    try:
        with TestClient(app) as client:
            assert client.get('/downstream').json()=={'result':'ok'}
            spans=exporter.get_finished_spans();assert len(spans)==2
            outgoing,incoming=spans
            assert outgoing.kind==SpanKind.CLIENT and incoming.kind==SpanKind.SERVER
            assert outgoing.parent.span_id==incoming.context.span_id and outgoing.context.trace_id==incoming.context.trace_id
            expected='00-'+f'{outgoing.context.trace_id:032x}'+'-'+f'{outgoing.context.span_id:016x}'+'-'+f'{int(outgoing.context.trace_flags):02x}'
            assert received[0]['Traceparent']==expected
            assert 'token' not in outgoing.to_json() and 'secret' not in outgoing.to_json() and '127.0.0.1' not in outgoing.to_json()
    finally:
        instrumentor.uninstrument();server.shutdown();server.server_close();thread.join(timeout=5)
    assert not thread.is_alive()


def test_exporter_projection_preserves_identity_times_kind_and_result_but_not_events():
    memory=InMemorySpanExporter();safe=PrivateSpanExporter(memory)
    provider=TracerProvider(resource=Resource({'service.name':'iot-drift-api'}));provider.add_span_processor(SimpleSpanProcessor(safe))
    tracer=provider.get_tracer('test')
    with tracer.start_as_current_span('GET private URL',kind=SpanKind.CLIENT) as span:
        span.set_attributes({'http.request.method':'POST','http.response.status_code':503,'http.url':'https://private?token=secret'})
        span.add_event('secret event',{'password':'secret'});span.set_status(Status(StatusCode.ERROR,'secret exception'))
        identity=span.get_span_context()
    actual=memory.get_finished_spans()[0]
    assert actual.name=='POST' and actual.context==identity and actual.end_time>actual.start_time
    assert actual.attributes=={'http.request.method':'POST','http.response.status_code':503}
    assert actual.status.status_code==StatusCode.ERROR and actual.status.description is None and not actual.events and not actual.links
    assert safe.force_flush(10) is True
    provider.shutdown()


@pytest.mark.parametrize('method,expected',[(v,v) for v in ['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS','HTTP']]+[('BANANA','HTTP'),(None,'HTTP')])
def test_exporter_names_have_bounded_method_fallback(method,expected):
    memory=InMemorySpanExporter();provider=TracerProvider();provider.add_span_processor(SimpleSpanProcessor(PrivateSpanExporter(memory)))
    with provider.get_tracer('test').start_as_current_span('private path') as span:
        if method is not None:span.set_attribute('http.method',method)
    assert memory.get_finished_spans()[0].name==expected
    provider.shutdown()


@pytest.mark.parametrize('value',[ENDPOINT.replace('http:','https:'),ENDPOINT.replace('iot-jaeger','other'),ENDPOINT.replace('4318','4317'),ENDPOINT.replace('4318','4319'),ENDPOINT.replace(':4318',''),ENDPOINT+'?token=secret',ENDPOINT+'#fragment',ENDPOINT.replace('http://','http://u:p@'),ENDPOINT.replace('http://','http://:p@'),ENDPOINT.replace('http://','http://u@'),ENDPOINT.replace('/v1/traces','/'), 'http://localhost:4318/v1/traces'])
def test_export_cannot_be_redirected_to_public_or_credentialed_endpoint(value):
    with pytest.raises(ValueError):private_endpoint(value)


def test_endpoint_valid_and_unknown_or_duplicate_instrumentation_rejected(instrumented):
    assert private_endpoint(ENDPOINT)==ENDPOINT
    app,_=instrumented
    with pytest.raises(ValueError,match='already'):observe(app,'feature')
    with pytest.raises(ValueError,match='Unknown'):observe(FastAPI(),'arbitrary')


def test_original_startup_failure_remains_failure_and_cleans_providers():
    calls=[]
    @asynccontextmanager
    async def lifespan(app):
        calls.append('startup');raise ValueError('invalid model seal');yield
    app=FastAPI(lifespan=lifespan);exporter=InMemorySpanExporter();observe(app,'predictor',exporter,SimpleSpanProcessor)
    closed=[]
    app.state.observability['traces'].shutdown=lambda:closed.append('traces')
    app.state.observability['meters'].shutdown=lambda:closed.append('meters')
    with pytest.raises(ValueError,match='model seal'):
        with TestClient(app):pass
    assert calls==['startup'] and closed==['traces','meters']


def test_default_exporter_uses_private_endpoint_bounded_timeout_and_empty_headers(monkeypatch):
    calls=[]
    monkeypatch.setenv('IOT_TRACE_ENDPOINT',ENDPOINT)
    def factory(**kwargs):calls.append(kwargs);return InMemorySpanExporter()
    monkeypatch.setattr('services.observed.OTLPSpanExporter',factory)
    app=observe(FastAPI(),'drift',processor_factory=SimpleSpanProcessor)
    with TestClient(app) as client:assert client.get('/telemetry/metrics').status_code==200
    assert calls==[{'endpoint':ENDPOINT,'headers':{},'timeout':2}]


@pytest.mark.parametrize('kind',['feature','drift','predictor'])
def test_deployed_factory_uses_selected_existing_api_and_instruments_outgoing_calls(monkeypatch,kind):
    import importlib
    business=FastAPI();calls=[];provider=object()
    module=importlib.import_module('services.'+('feature_api' if kind=='feature' else 'drift_api' if kind=='drift' else 'predictor')+'.app')
    monkeypatch.setattr(module,'create_app',lambda:business)
    def install(app,selected):
        assert app is business and selected==kind
        app.state.observability={'traces':provider};return app
    monkeypatch.setattr('services.observed.observe',install)
    monkeypatch.setattr('services.observed.URLLibInstrumentor',lambda:SimpleNamespace(instrument=lambda **kwargs:calls.append(kwargs)))
    monkeypatch.setenv('IOT_API_SERVICE',kind)
    assert create_app() is business and calls==[{'tracer_provider':provider}]


def test_factory_requires_explicit_known_service(monkeypatch):
    monkeypatch.setenv('IOT_API_SERVICE','unknown')
    with pytest.raises(ValueError,match='Unknown'):create_app()
    monkeypatch.delenv('IOT_API_SERVICE')
    with pytest.raises(KeyError):create_app()


def test_export_failure_and_flush_deadline_propagate_to_sdk_caller():
    """A caller must observe exporter failure and its own flush deadline, rather than a fabricated success."""
    seen={}
    class Destination:
        def export(self,spans):seen['batch']=spans;return SpanExportResult.FAILURE
        def force_flush(self,timeout_millis):seen['timeout']=timeout_millis;return False
        def shutdown(self):seen['closed']=True
    memory=InMemorySpanExporter();provider=TracerProvider(resource=Resource({'service.name':'iot-predictor-api'}));provider.add_span_processor(SimpleSpanProcessor(memory))
    with provider.get_tracer('wire-test','1.0').start_as_current_span('private',kind=SpanKind.CLIENT) as span:
        span.set_attribute('http.method','PUT');span.set_attribute('http.status_code',500)
    raw=memory.get_finished_spans()[0];safe=PrivateSpanExporter(Destination())
    assert safe.export([raw])==SpanExportResult.FAILURE
    assert safe.force_flush(17) is False and seen['timeout']==17
    projected=seen['batch'][0]
    assert projected.resource==raw.resource and projected.instrumentation_scope==raw.instrumentation_scope
    assert projected.context==raw.context and projected.kind==raw.kind and projected.start_time==raw.start_time and projected.end_time==raw.end_time
    safe.shutdown();assert seen['closed'] is True;provider.shutdown()


@pytest.mark.parametrize('kind',['drift','predictor'])
def test_actual_other_api_factory_keeps_service_identity_and_response(kind,tmp_path):
    """Every API needs a distinct service identity and genuine business response, not just a factory selection mock."""
    if kind=='drift':
        from tests.api.test_drift_api import bundle,window
        from services.drift_api.app import create_app as factory
        from services.drift_api.runtime import DriftRuntime
        runtime=DriftRuntime(bundle(),tmp_path/'windows.db');app=factory(runtime)
        path='/v1/drift:compare';body=window(runtime.detector.reference_id)
    else:
        from services.predictor.app import create_app as factory
        from tests.api.test_feature_api import BUNDLE,GOLDEN
        app=factory(BUNDLE);path='/v1/models/iot-pump-failure-v1:predict';body={'instances':[GOLDEN['samples'][0]['features']]}
    exporter=InMemorySpanExporter();observe(app,kind,exporter,SimpleSpanProcessor)
    with TestClient(app) as client:
        reply=client.post(path,json=body);assert reply.status_code==200
        counts=samples(client.get('/telemetry/metrics').text,'http_server_duration_milliseconds_count')
        assert len(counts)==1 and counts[0].value==1 and counts[0].labels['service_name']=='iot-'+kind+'-api'
        span=exporter.get_finished_spans()[0]
        assert span.resource.attributes=={'service.name':'iot-'+kind+'-api','deployment.environment.name':'coursework-synthetic-lab'}
