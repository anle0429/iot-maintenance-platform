"""Expose private two-model routing, replay-safe experiment readbacks and Prometheus monitoring."""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
import asyncio,json,os
from fastapi import FastAPI,Header,HTTPException,Path as APIPath,Response,Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel,ConfigDict
from prometheus_client import CONTENT_TYPE_LATEST
from services.ab_api.runtime import ABRuntime,TrialConflict,TrialCapacity,UnknownMachine
from services.ab_api.metrics import exposition
from services.feature_api.runtime import FeatureRuntime,FeastKServeBackend


class TrialRequest(BaseModel):
    """Accept an empty request object; raw features, variant overrides and future labels are forbidden."""
    model_config=ConfigDict(extra='forbid')


def native_runtime():
    """Load existing native Feast state and two fixed private predictor endpoints; callers cannot override them."""
    config=json.loads(Path(os.environ['IOT_AB_CONFIG']).read_text())
    runtimes={}
    for name in ['baseline','candidate']:
        backend=FeastKServeBackend(os.environ['IOT_SERVING_STATE'],config[name]['url'])
        runtimes[name]=FeatureRuntime(backend.context,backend,model_pin=config[name]['pin'])
    return ABRuntime(config['experiment'],runtimes,json.loads(Path(os.environ['IOT_DRIFT_REFERENCE']).read_text()),os.environ['IOT_AB_DATABASE'])


def create_app(runtime=None):
    """Inject native-independent fixtures for verification; default startup constructs actual SDK backends."""
    @asynccontextmanager
    async def lifespan(app):
        app.state.runtime=runtime if runtime is not None else native_runtime();app.state.slots=asyncio.Semaphore(4)
        yield
        app.state.runtime=None
    app=FastAPI(title='IoT two-model experiment',version='1.0.0',lifespan=lifespan);app.state.runtime=None

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        """Omit echoed input so NaN/Infinity or rejected labels cannot break error JSON serialization."""
        return JSONResponse({'detail':[{key:error[key] for key in ['loc','msg','type']} for error in exc.errors()]},status_code=422)

    @app.middleware('http')
    async def bound_body(request:Request,call_next):
        """Require fixed bounded framing before parsing any POST body."""
        if request.method=='POST':
            length=request.headers.get('content-length','')
            if not length.isdigit():return JSONResponse({'detail':'Fixed Content-Length required'},status_code=411)
            if int(length)>256:return JSONResponse({'detail':'Trial body exceeds256bytes'},status_code=413)
        return await call_next(request)

    async def invoke(method,*args):
        """Bound thread work and return stable public failures without exposing SDK paths or secrets."""
        if app.state.runtime is None:raise HTTPException(503,'Experiment is not ready')
        try:
            async with app.state.slots:return await asyncio.to_thread(getattr(app.state.runtime,method),*args)
        except UnknownMachine as error:raise HTTPException(404,'Unknown machine') from error
        except TrialConflict as error:raise HTTPException(409,'Experiment/request identity conflict') from error
        except TrialCapacity as error:raise HTTPException(507,'Experiment capacity reached') from error
        except Exception as error:raise HTTPException(503,'Experiment data is unavailable or failed validation') from error

    @app.get('/health/live')
    async def live():
        """Separate process liveness from the model pair and feature lease."""
        return {'live':True}

    @app.get('/health/ready')
    async def ready():
        """Require real feature/model reads through both variants without changing sample counts."""
        return await invoke('ready')

    @app.post('/v1/machines/{machine_id}:predict')
    async def predict(machine_id:Annotated[str,APIPath(pattern=r'^P-[0-9]{3,4}$')],body:TrialRequest,
            request_id:Annotated[str,Header(alias='X-Request-ID',pattern=r'^[A-Za-z0-9_:\-]{1,100}$')]):
        """Route by stable machine cohort; request IDs provide replay identity, never variant choice."""
        result=await invoke('predict',machine_id,request_id)
        return JSONResponse(result,status_code=result['status'])

    @app.get('/v1/experiment')
    async def experiment():
        """Read durable trial counts, raw observations and marginal drift without accepting labels."""
        return await invoke('snapshot')

    @app.get('/metrics')
    async def metrics():
        """Expose a single consistent snapshot using bounded variant/version/feature labels."""
        snapshot=await invoke('snapshot')
        return Response(exposition(snapshot),media_type=CONTENT_TYPE_LATEST)

    return app
