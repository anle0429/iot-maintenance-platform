"""Expose async KServe V1 prediction, startup integrity checks, health and bounded-label telemetry."""

from contextlib import asynccontextmanager
from pathlib import Path
import os,time

from fastapi import FastAPI,HTTPException,Request,Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CollectorRegistry,Counter,Histogram,generate_latest,CONTENT_TYPE_LATEST

from services.predictor.contracts import PredictRequest
from services.predictor.runtime import PumpPredictor


def create_app(bundle:Path|None=None)->FastAPI:
    """Construct an isolated application; health never implies an unavailable model is ready."""
    location=bundle or Path(os.environ.get('IOT_MODEL_BUNDLE','models/pump-failure-v1'))
    registry=CollectorRegistry()
    requests=Counter('iot_predictor_requests_total','Completed inference requests',['outcome'],registry=registry)
    latency=Histogram('iot_predictor_request_seconds','Inference HTTP duration',registry=registry)

    @asynccontextmanager
    async def lifespan(app):
        'Load the immutable predictor bundle once and release the application reference on shutdown.'
        app.state.predictor=PumpPredictor(location)
        yield
        app.state.predictor=None

    app=FastAPI(title='IoT pump predictor',version='1.0.0',lifespan=lifespan)
    app.state.predictor=None

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        """Return valid JSON for nonfinite inputs and omit echoed feature values from errors."""
        return JSONResponse(status_code=422,content={'detail':[
            {k:error[k] for k in ['loc','msg','type']} for error in exc.errors()]})

    @app.middleware('http')
    async def measure(request:Request,call_next):
        """Count every prediction outcome including validation errors without unbounded path labels."""
        if request.url.path.endswith(':predict'):
            started=time.perf_counter()
            response=await call_next(request)
            requests.labels(outcome='success' if response.status_code<400 else 'error').inc()
            latency.observe(time.perf_counter()-started)
            return response
        return await call_next(request)

    def available(name=None):
        """Return503 until the model is ready and404 for a different model route."""
        predictor=app.state.predictor
        if predictor is None:raise HTTPException(503,'Model is not ready')
        if name is not None and name!=predictor.lineage['model_name']:raise HTTPException(404,'Unknown model')
        return predictor

    @app.get('/health/live')
    async def live():
        """Report the HTTP process liveness without touching the model or external dependencies."""
        return {'live':True}

    @app.get('/health/ready')
    async def ready():
        """Report readiness only after model byte/schema checks at startup."""
        return {'ready':True,'version':available().lineage['version']}

    @app.get('/v1/models/{model_name}')
    async def metadata(model_name:str):
        """Expose the pinned model metadata used to trace an individual prediction."""
        return available(model_name).metadata()

    @app.post('/v1/models/{model_name}:predict')
    async def predict(model_name:str,payload:PredictRequest):
        """Predict only from explicit valid feature vectors; feature retrieval is a separate service."""
        predictor=available(model_name)
        return {'predictions':predictor.predict(payload.instances),'model':predictor.metadata()}

    @app.get('/metrics',include_in_schema=False)
    async def metrics():
        """Expose request counts/errors/latency for Prometheus scraping."""
        return Response(generate_latest(registry),media_type=CONTENT_TYPE_LATEST)

    return app
