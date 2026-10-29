"""Add bounded HTTP metrics and private OTLP traces around unchanged API factories, without payloads or secrets."""
from contextlib import asynccontextmanager
import os
from urllib.parse import urlsplit
from fastapi import Response
from prometheus_client import CollectorRegistry,generate_latest,CONTENT_TYPE_LATEST
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.urllib import URLLibInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider,ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter,BatchSpanProcessor
from opentelemetry.trace import Status

SERVICES={'feature','drift','predictor'}
EXCLUDED=r'^https?://[^/]+/(?:metrics|telemetry/metrics|health/(?:live|ready))(?:\?.*)?$'
ATTRIBUTES={'http.method','http.status_code','http.route','http.request.method','http.response.status_code'}


class PrivateSpanExporter(SpanExporter):
    """Export an allowlist projection; preserve identity/times/parent but remove URLs, exceptions, baggage and headers."""
    def __init__(self,exporter):
        """Retain the injected destination without installing any global provider."""
        self.exporter=exporter

    def export(self,spans):
        """Project through public ReadableSpan fields before any trace reaches the network/exporter."""
        clean=[]
        for span in spans:
            attributes={k:v for k,v in span.attributes.items() if k in ATTRIBUTES}
            method=attributes.get('http.request.method',attributes.get('http.method','HTTP'))
            method=method if method in {'GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS','HTTP'} else 'HTTP'
            route=attributes.get('http.route')
            name=method+(' '+route if route else '')
            clean.append(ReadableSpan(name=name,context=span.context,parent=span.parent,resource=span.resource,
                attributes=attributes,events=(),links=(),kind=span.kind,status=Status(span.status.status_code),
                start_time=span.start_time,end_time=span.end_time,instrumentation_scope=span.instrumentation_scope))
        return self.exporter.export(clean)

    def shutdown(self):
        """Close the actual exporter with the provider; no new worker or retry loop is created here."""
        self.exporter.shutdown()

    def force_flush(self,timeout_millis=30000):
        """Delegate flushing without suppressing failure reports."""
        return self.exporter.force_flush(timeout_millis)


def private_endpoint(value):
    """Accept only the declared cluster-local Jaeger receiver, without credentials, query or fragments."""
    parts=urlsplit(value)
    if (parts.scheme!='http' or parts.hostname!='iot-jaeger.iot-observability.svc.cluster.local'
            or parts.port!=4318 or parts.path!='/v1/traces' or parts.username or parts.password or parts.query or parts.fragment):
        raise ValueError('Use the private coursework Jaeger OTLP endpoint')
    return value


def observe(app,service,exporter=None,processor_factory=BatchSpanProcessor):
    """Install independent providers on one single-worker API; allow a memory exporter for deterministic tests."""
    if service not in SERVICES:raise ValueError('Unknown coursework API service')
    if getattr(app.state,'observability',None) is not None:raise ValueError('This API already has telemetry')
    endpoint=private_endpoint(os.environ['IOT_TRACE_ENDPOINT']) if exporter is None else None
    resource=Resource({'service.name':'iot-'+service+'-api','deployment.environment.name':'coursework-synthetic-lab'})
    registry=CollectorRegistry()
    reader=PrometheusMetricReader(registry=registry,scope_info_enabled=False,
        resource_attribute_filter=lambda key:key=='service.name')
    # The pinned ASGI instrumentor puts the template in http.target for legacy duration metrics.
    # It never uses the raw path/query here; trace attributes retain the narrower allowlist above.
    meters=MeterProvider(resource=resource,metric_readers=[reader],views=[View(instrument_name='*',attribute_keys=ATTRIBUTES|{'http.target'})])
    provider=TracerProvider(resource=resource)
    if exporter is None:
        exporter=OTLPSpanExporter(endpoint=endpoint,headers={},timeout=2)
    provider.add_span_processor(processor_factory(PrivateSpanExporter(exporter)))
    original_lifespan=app.router.lifespan_context

    @asynccontextmanager
    async def lifecycle(current):
        """Keep the original model/store startup checks and close telemetry after request drain."""
        try:
            async with original_lifespan(current):yield
        finally:
            provider.shutdown();meters.shutdown()

    async def metrics():
        """Scrape only this process; do not count scrapes as business requests."""
        return Response(generate_latest(registry),media_type=CONTENT_TYPE_LATEST)

    app.router.lifespan_context=lifecycle
    app.add_api_route('/telemetry/metrics',metrics,include_in_schema=False)
    FastAPIInstrumentor.instrument_app(app,tracer_provider=provider,meter_provider=meters,
        excluded_urls=EXCLUDED,exclude_spans=['receive','send'],http_capture_headers_server_request=[],http_capture_headers_server_response=[])
    app.state.observability={'traces':provider,'meters':meters,'registry':registry}
    return app


def create_app():
    """Select one fixed factory; URLs/model/state remain the existing API's independently validated inputs."""
    kind=os.environ['IOT_API_SERVICE']
    if kind=='feature':from services.feature_api.app import create_app as factory
    elif kind=='drift':from services.drift_api.app import create_app as factory
    elif kind=='predictor':from services.predictor.app import create_app as factory
    else:raise ValueError('Unknown coursework API service')
    app=observe(factory(),kind)
    URLLibInstrumentor().instrument(tracer_provider=app.state.observability['traces'])
    return app
