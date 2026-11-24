"""Private HTTP service for ranked passages from a sealed maintenance knowledge snapshot."""
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, HTTPException, Security
from fastapi.responses import Response
from fastapi.security import HTTPBearer
from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST

from services.mcp_tools.auth import BoundedBearerMiddleware
from services.mcp_tools.snapshot import RUNTIME_STATE
from services.rag_search.contracts import SearchRequest, SearchResult, read_service_token
from services.rag_search.reader import KnowledgeFailure, KnowledgeReader

SEARCH_QUEUE_WAIT_SECONDS = 1
SEARCH_MAX_PENDING = 2  # One active reader plus one queued request.


def create_app(reader=None, token=None):
    """Load the per-replica store once and expose a credential-scoped search endpoint."""
    if token is None:
        token = read_service_token()
    if len(token) < 32:
        raise ValueError('A scoped RAG search credential is required')

    registry = CollectorRegistry()
    attempts = Counter('iot_rag_search_attempts_total', 'Authenticated, schema-valid search arrivals.', registry=registry)
    outcomes = Counter('iot_rag_search_outcomes_total', 'Search responses by bounded result.', ['result'], registry=registry)
    success, busy, invalid, unavailable = (outcomes.labels(result) for result in ('success', 'busy', 'invalid', 'unavailable'))
    queued = Gauge('iot_rag_search_queued', 'Requests waiting for the local reader.', registry=registry)
    active = Gauge('iot_rag_search_active', 'Requests running in the local reader.', registry=registry)

    @asynccontextmanager
    async def lifespan(app):
        """Admit traffic after initializing the encoder and the verified local copy."""
        app.state.reader = reader if reader is not None else await anyio.to_thread.run_sync(
            KnowledgeReader, RUNTIME_STATE, '/workspace'
        )
        app.state.admission = anyio.CapacityLimiter(SEARCH_MAX_PENDING)
        app.state.search_slots = anyio.CapacityLimiter(1)
        try:
            yield
        finally:
            app.state.reader = None

    app = FastAPI(title='IoT RAG Search', version='1.0.0', lifespan=lifespan)
    app.add_middleware(BoundedBearerMiddleware, token=token)

    @app.get('/health/live')
    async def live():
        """Report process liveness independently of read access to the initialized snapshot."""
        return {'live': True, 'service': 'rag-search'}

    @app.get('/health/ready')
    async def ready():
        """Report whether the verified store and its pinned encoder have loaded."""
        if getattr(app.state, 'reader', None) is None:
            raise HTTPException(503, 'Knowledge snapshot is unavailable')
        return {'ready': True, 'service': 'rag-search'}

    @app.get('/metrics')
    async def metrics():
        """Expose per-pod demand and saturation without request content, credentials or machine IDs."""
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    def overloaded():
        """Return a bounded, retryable response when this replica cannot admit another search."""
        busy.inc()
        return HTTPException(429, 'Knowledge search is busy', headers={'Retry-After': '1'})

    @app.post('/v1/knowledge:search', response_model=SearchResult, dependencies=[Security(HTTPBearer())])
    async def search(request: SearchRequest):
        """Search the local snapshot, returning only ranked, source-checked passages."""
        attempts.inc()
        if getattr(app.state, 'reader', None) is None:
            unavailable.inc()
            raise HTTPException(503, 'Knowledge snapshot is unavailable')
        admission, slots = app.state.admission, app.state.search_slots
        try:
            admission.acquire_nowait()
        except anyio.WouldBlock:
            raise overloaded() from None
        try:
            with queued.track_inprogress(), anyio.move_on_after(SEARCH_QUEUE_WAIT_SECONDS) as deadline:
                await slots.acquire()
            if deadline.cancelled_caught:
                raise overloaded()
            try:
                with active.track_inprogress():
                    raw = await anyio.to_thread.run_sync(app.state.reader.search, request)
                result = SearchResult.model_validate(raw)
                if len(result.matches) != request.top_k:
                    raise ValueError('Incomplete knowledge ranking')
            except KnowledgeFailure as error:
                (invalid if error.status == 422 else unavailable).inc()
                raise HTTPException(error.status, str(error)) from None
            except Exception:
                unavailable.inc()
                raise HTTPException(503, 'Knowledge store is unavailable or invalid') from None
            finally:
                slots.release()
        finally:
            admission.release()
        success.inc()
        return result

    return app
