"""Expose bounded async machine feature/prediction reads with fail-closed freshness and operating-state checks."""
from contextlib import asynccontextmanager
from typing import Annotated
import asyncio,os
from fastapi import FastAPI,HTTPException,Path
from services.feature_api.runtime import FeatureRuntime,FeastKServeBackend,NotOperating


def create_app(runtime=None):
    """Use dependency injection for tests while production constructs the actual native backend at startup."""
    @asynccontextmanager
    async def lifespan(app):
        if runtime is None:
            backend=FeastKServeBackend(os.environ['IOT_SERVING_STATE'],os.environ['IOT_KSERVE_URL'])
            app.state.runtime=FeatureRuntime(backend.context,backend)
        else:app.state.runtime=runtime
        app.state.slots=asyncio.Semaphore(16)
        yield
        app.state.runtime=None
    app=FastAPI(title='IoT machine feature API',version='1.0.0',lifespan=lifespan)
    app.state.runtime=None

    async def invoke(method,machine):
        """Keep sync SDK/network work off the event loop and bound in-flight backend operations."""
        if app.state.runtime is None:raise HTTPException(503,'Feature service is not ready')
        try:
            async with app.state.slots:return await asyncio.to_thread(getattr(app.state.runtime,method),machine)
        except KeyError as error:
            if machine not in app.state.runtime.context['machines']:raise HTTPException(404,'Unknown machine') from error
            raise HTTPException(503,'Incomplete upstream response') from error
        except NotOperating as error:raise HTTPException(409,str(error)) from error
        except Exception as error:raise HTTPException(503,'Feature/model data is unavailable or failed validation') from error

    @app.get('/health/live')
    async def live():
        """Report process liveness independently of downstream availability."""
        return {'live':True}

    @app.get('/health/ready')
    async def ready():
        """Verify a real online lookup and current demo lease before advertising readiness."""
        if app.state.runtime is None:raise HTTPException(503,'Feature service is not ready')
        machine=next(iter(app.state.runtime.context['machines']))
        await invoke('features',machine)
        return {'ready':True,'clock_mode':'simulation','as_of':app.state.runtime.as_of.isoformat()}

    @app.get('/v1/machines/{machine_id}/features')
    async def features(machine_id:Annotated[str,Path(pattern=r'^P-[0-9]{3,4}$')]):
        """Read the native feature vector, source times and separately observed operating status."""
        return await invoke('features',machine_id)

    @app.post('/v1/machines/{machine_id}:predict')
    async def predict(machine_id:Annotated[str,Path(pattern=r'^P-[0-9]{3,4}$')]):
        """Retrieve and validate features before calling the private pinned KServe model."""
        return await invoke('predict',machine_id)

    return app
