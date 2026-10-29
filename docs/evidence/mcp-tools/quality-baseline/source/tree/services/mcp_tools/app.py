"""Serve role-isolated asynchronous REST and native MCP tools with health, bearer auth and bounded telemetry."""
from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from typing import Annotated
import os
import time
from fastapi import FastAPI, HTTPException, Path, Security
from fastapi.security import HTTPBearer
from fastapi.responses import Response
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from services.mcp_tools.auth import BoundedBearerMiddleware
from services.mcp_tools.contracts import MachineRequest, SearchRequest, DriftRequest, MACHINE_PATTERN
from services.mcp_tools.runtime import ToolRuntime, ToolFailure

ROLE_TOOLS = {'retrieval': {'get_machine_features','predict_failure','search_knowledge'}, 'drift': {'assess_feature_drift'}}


def create_app(runtime=None, role=None, token=None):
    """Inject bounded test adapters or bind the fixed native backends and mounted role-specific credential."""
    role = role or os.environ.get('IOT_MCP_ROLE','retrieval')
    if role not in ROLE_TOOLS: raise ValueError('Unknown MCP role')
    if token is None: token = FilePath(os.environ['IOT_MCP_TOKEN_FILE']).read_text().strip()
    if len(token) < 32: raise ValueError('A scoped bearer secret is required')
    runtime = runtime or ToolRuntime(role, os.environ.get('IOT_DRIFT_REFERENCE_ID',''))
    server = MCPServer('iot-'+role, version='1.0.0', log_level='WARNING')
    registry = CollectorRegistry()
    calls = Counter('iot_mcp_tool_calls_total','Completed tool calls by bounded name and result.',['role','tool','result'],registry=registry)
    latency = Histogram('iot_mcp_tool_seconds','Tool execution time.',['role','tool'],registry=registry)
    child = server.streamable_http_app(stateless_http=True,json_response=True,max_request_body_size=65_536,
        transport_security=TransportSecuritySettings(allowed_hosts=['testserver','127.0.0.1:*','localhost:*',
            'iot-retrieval-mcp.iot-agents.svc.cluster.local','iot-retrieval-mcp.iot-agents.svc.cluster.local:*',
            'iot-drift-mcp.iot-agents.svc.cluster.local','iot-drift-mcp.iot-agents.svc.cluster.local:*'],
            allowed_origins=['http://127.0.0.1:*','http://localhost:*']))

    @asynccontextmanager
    async def lifespan(app):
        """Warm the native dependency once and run the MCP transport lifecycle inside the parent FastAPI app."""
        await runtime.start()
        try:
            async with server.session_manager.run(): yield
        finally:
            await runtime.close()

    app = FastAPI(title='IoT '+role+' tools',version='1.0.0',lifespan=lifespan)
    app.add_middleware(BoundedBearerMiddleware,token=token)
    bearer = HTTPBearer(auto_error=False)

    async def invoke(name, payload):
        """Dispatch only a role's declared tools and record bounded success/failure counters."""
        if name not in ROLE_TOOLS[role]: raise ToolFailure(404,'Tool is not available in this role')
        method = {'get_machine_features':'features','predict_failure':'predict','search_knowledge':'search','assess_feature_drift':'drift'}[name]
        started = time.perf_counter(); result = 'failure'
        try:
            value = await getattr(runtime,method)(payload); result = 'success'; return value
        finally:
            calls.labels(role,name,result).inc(); latency.labels(role,name).observe(time.perf_counter()-started)

    async def rest(name, payload):
        """Map safe native failure categories to HTTP without returning private upstream diagnostics."""
        try: return await invoke(name,payload)
        except ToolFailure as error: raise HTTPException(error.status,str(error)) from None

    async def mcp_call(name, payload):
        """Return a real MCP tool error when the operation fails rather than a successful-looking JSON error."""
        try: return await invoke(name,payload)
        except ToolFailure as error: raise ToolError(str(error.status)+': '+str(error)) from None

    @app.get('/health/live')
    async def live():
        """Report process liveness independently of native backend readiness."""
        return {'live':True,'role':role}

    @app.get('/health/ready')
    async def ready():
        """Refuse readiness when actual data/reference dependencies are unavailable."""
        try: return dict(await runtime.ready(),pod=os.environ.get('HOSTNAME','local'))
        except ToolFailure as error: raise HTTPException(503,str(error)) from None

    @app.get('/metrics')
    async def metrics():
        """Expose per-instance bounded tool counters and duration histograms without input text or IDs."""
        return Response(generate_latest(registry),media_type=CONTENT_TYPE_LATEST)

    if role == 'retrieval':
        @app.get('/v1/machines/{machine_id}/features',dependencies=[Security(bearer)])
        async def features(machine_id:Annotated[str,Path(pattern=MACHINE_PATTERN)]):
            """Read one machine through the existing native Feature API."""
            return await rest('get_machine_features',MachineRequest(machine_id=machine_id))

        @app.post('/v1/machines/{machine_id}:predict',dependencies=[Security(bearer)])
        async def predict(machine_id:Annotated[str,Path(pattern=MACHINE_PATTERN)]):
            """Call the pinned prediction path while preserving operating-state/freshness rejection."""
            return await rest('predict_failure',MachineRequest(machine_id=machine_id))

        @app.post('/v1/knowledge:search',dependencies=[Security(bearer)])
        async def search(request:SearchRequest):
            """Return bounded source-traceable reference passages without exposing embedding vectors to the model."""
            return await rest('search_knowledge',request)

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
        async def get_machine_features(request:MachineRequest)->dict:
            """Read a simulated pump's current feature snapshot with event time, preparation time and source provenance."""
            return await mcp_call('get_machine_features',request)

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
        async def predict_failure(request:MachineRequest)->dict:
            """Read the synthetic failure-risk prediction for an operating pump; preserve stopped and stale-data errors."""
            return await mcp_call('predict_failure',request)

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
        async def search_knowledge(request:SearchRequest)->dict:
            """Retrieve authored IoT reference passages and exact chunk/source identities for a bounded question."""
            return await mcp_call('search_knowledge',request)
    else:
        @app.post('/v1/drift:assess',dependencies=[Security(bearer)])
        async def assess(request:DriftRequest):
            """Compare 32..200 distinct machines against the pinned training reference without accepting labels."""
            return await rest('assess_feature_drift',request)

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=True,openWorldHint=False))
        async def assess_feature_drift(request:DriftRequest)->dict:
            """Assess label-free feature drift for a named cohort/window; input drift alone does not measure accuracy loss."""
            return await mcp_call('assess_feature_drift',request)

    app.mount('/',child)
    return app
