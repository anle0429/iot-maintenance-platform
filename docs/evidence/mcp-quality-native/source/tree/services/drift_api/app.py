"""Expose async drift comparisons, Kubernetes health probes and bounded request validation."""
from contextlib import asynccontextmanager
import asyncio
import json
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from services.drift_api.contracts import CompareRequest
from services.drift_api.runtime import DriftRuntime, WindowConflict, StoreFull


def create_app(runtime=None):
    """Inject a fixture in tests or load a mounted immutable training reference in deployment."""
    @asynccontextmanager
    async def lifespan(app):
        'Open the injected or persistent drift runtime and release its application reference on shutdown.'
        app.state.runtime = runtime or DriftRuntime(json.loads(Path(os.environ['IOT_DRIFT_REFERENCE']).read_text()), os.environ['IOT_DRIFT_DATABASE'])
        app.state.slots = asyncio.Semaphore(4)
        yield
        app.state.runtime = None

    app = FastAPI(title='IoT label-free drift API', version='1.0.0', lifespan=lifespan)
    app.state.runtime = None

    @app.middleware('http')
    async def bound_body(request, call_next):
        """Require fixed bounded request framing for JSON comparisons, including direct service calls."""
        if request.method == 'POST':
            from fastapi.responses import JSONResponse
            length = request.headers.get('content-length', '')
            if not length.isdigit():
                return JSONResponse({'detail': 'A fixed Content-Length is required'}, status_code=411)
            if int(length) > 1_048_576:
                return JSONResponse({'detail': 'Request exceeds1MiB'}, status_code=413)
        return await call_next(request)

    async def invoke(method, *args):
        """Bound CPU/database work and keep the ASGI event loop available for health checks."""
        if app.state.runtime is None:
            raise HTTPException(503, 'Drift service is not ready')
        try:
            async with app.state.slots:
                return await asyncio.to_thread(getattr(app.state.runtime, method), *args)
        except WindowConflict as error:
            raise HTTPException(409, str(error)) from error
        except StoreFull as error:
            raise HTTPException(507, str(error)) from error
        except Exception as error:
            raise HTTPException(503, 'Drift reference or persistent store is unavailable') from error

    @app.get('/health/live')
    async def live():
        """Report process liveness independently of persistent state."""
        return {'live': True}

    @app.get('/health/ready')
    async def ready():
        """Check reference identity and the actual assessment store."""
        return await invoke('ready')

    @app.post('/v1/drift:compare')
    async def compare(request: CompareRequest):
        """Assess a label-free window exactly once, with immutable reference and window identities."""
        return await invoke('compare', request)

    from services.drift_api.events import install
    install(app,invoke)
    return app
