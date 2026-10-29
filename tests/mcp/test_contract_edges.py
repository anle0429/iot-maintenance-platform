"""Check externally observable auth, MCP metadata, concurrency and native-adapter failure contracts at their edges."""
import asyncio
import json
import pytest

pytest.importorskip('mcp', reason='Install the dedicated MCP test dependencies')
import httpx2
from fastapi.testclient import TestClient
from services.mcp_tools.app import create_app
from services.mcp_tools.auth import BoundedBearerMiddleware
from services.mcp_tools.contracts import MachineRequest, SearchRequest, DriftRequest
from services.mcp_tools.runtime import ToolRuntime, ToolFailure, KnowledgeReader
from test_tools import TOKEN, HEADERS, REFERENCE, machine, SmallKnowledge, make_backends


@pytest.mark.parametrize('size,valid', [(0, False), (31, False), (32, True), (33, True)])
def test_credential_length_acceptance(size, valid):
    """A configured credential must meet the documented minimum, including the exact accepted edge."""
    if valid:
        assert BoundedBearerMiddleware(None, 'x' * size).expected == ('Bearer ' + 'x' * size).encode()
    else:
        with pytest.raises(ValueError):
            BoundedBearerMiddleware(None, 'x' * size)


@pytest.mark.parametrize('size', [0, 65536])
def test_segmented_body_accepted_edges(size):
    """Accept exactly the allowed body size even when transport delivery splits it into several chunks."""
    async def scenario():
        """Pass actual ASGI messages through the guard and compare the content received by the downstream app."""
        data = b'x' * size
        incoming = [{'type': 'http.request', 'body': data[:size // 2], 'more_body': True},
                    {'type': 'http.request', 'body': data[size // 2:], 'more_body': False}]
        observed = []
        async def receive():
            """Supply the next real transport chunk."""
            return incoming.pop(0)
        async def app(scope, receive, send):
            """Observe the complete validated body once."""
            observed.append(await receive())
        async def send(value):
            """Fail if an accepted body unexpectedly produces an HTTP rejection."""
            raise AssertionError(value)
        await BoundedBearerMiddleware(app, TOKEN)({'type': 'http', 'path': '/mcp', 'method': 'POST',
            'headers': [(b'authorization', ('Bearer ' + TOKEN).encode()), (b'content-length', str(size).encode())]}, receive, send)
        assert observed == [{'type': 'http.request', 'body': data, 'more_body': False}]
    asyncio.run(scenario())


@pytest.mark.parametrize('role,tools', [('retrieval', {'get_machine_features', 'predict_failure', 'search_knowledge'}),
                                      ('drift', {'assess_feature_drift'})])
def test_production_factory_env_and_client_contract(role, tools, tmp_path, monkeypatch):
    """A mounted credential and environment-selected role must expose the intended client-visible tool schemas."""
    import services.mcp_tools.app as module
    client, observed, detector = make_backends(tmp_path)
    runtime = ToolRuntime(role, REFERENCE, SmallKnowledge(), client)
    token_file = tmp_path / 'credential'; token_file.write_text(TOKEN)
    monkeypatch.setenv('IOT_MCP_ROLE', role); monkeypatch.setenv('IOT_MCP_TOKEN_FILE', str(token_file))
    monkeypatch.setenv('IOT_DRIFT_REFERENCE_ID', REFERENCE)
    bindings = []
    def factory(configured_role, reference):
        """Observe the production factory's actual role/reference arguments without loading model weights."""
        bindings.append((configured_role, reference))
        return runtime
    monkeypatch.setattr(module, 'ToolRuntime', factory)
    with TestClient(create_app()) as api:
        assert bindings == [(role, REFERENCE)]
        assert api.get('/health/live').json() == {'live': True, 'role': role}
        ready = api.get('/health/ready').json()
        assert ready['ready'] is True and ready['role'] == role and ready['pod']
        for path in ['/docs', '/openapi.json', '/metrics']:
            assert api.get(path).status_code == 200
        rejected = api.get('/v1/unknown')
        assert rejected.status_code == 401 and rejected.headers['www-authenticate'] == 'Bearer'
        assert TOKEN not in rejected.text and TOKEN not in api.get('/openapi.json').text
        result = api.post('/mcp', headers={**HEADERS, 'Accept': 'application/json, text/event-stream'},
                          json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}).json()['result']['tools']
        assert {row['name'] for row in result} == tools
        for row in result:
            annotations = row['annotations']
            assert annotations['readOnlyHint'] is (role == 'retrieval')
            assert annotations['destructiveHint'] is False and annotations['idempotentHint'] is True
            assert annotations['openWorldHint'] is False
            assert row['inputSchema']['required'] == ['request']
            assert row['description'] and row['inputSchema']['type'] == 'object'


@pytest.mark.parametrize('status,expected', [(404, 404), (409, 409), (422, 422), (507, 507), (401, 503), (500, 503)])
def test_backend_failure_categories_are_preserved_without_private_body(status, expected):
    """Known business failures retain their status while other upstream failures become a safe unavailable result."""
    async def scenario():
        """Drive the real asynchronous adapter and close its injected transport."""
        transport = httpx2.MockTransport(lambda request: httpx2.Response(status, text='private-upstream-diagnostic'))
        runtime = ToolRuntime('retrieval', REFERENCE, SmallKnowledge(), httpx2.AsyncClient(transport=transport))
        try:
            with pytest.raises(ToolFailure) as error:
                await runtime.features(MachineRequest(machine_id='P-001'))
            assert error.value.status == expected and 'private-upstream-diagnostic' not in str(error.value)
        finally:
            await runtime.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['timeout', 'invalid-json', 'oversized'])
def test_failed_native_transport_is_not_success(failure):
    """A network timeout, malformed JSON or oversized native response cannot become a successful tool result."""
    async def scenario():
        """Use observable HTTP failure boundaries without involving external networking."""
        def handle(request):
            """Return the selected native failure partition."""
            if failure == 'timeout':
                raise httpx2.ReadTimeout('private URL and details', request=request)
            return httpx2.Response(200, content=b'x' * 1_048_577 if failure == 'oversized' else b'{invalid')
        runtime = ToolRuntime('retrieval', REFERENCE, SmallKnowledge(), httpx2.AsyncClient(transport=httpx2.MockTransport(handle)))
        try:
            with pytest.raises(ToolFailure) as error:
                await runtime.features(MachineRequest(machine_id='P-001'))
            assert error.value.status == 503 and 'private URL' not in str(error.value)
        finally:
            await runtime.close()
    asyncio.run(scenario())


def test_native_concurrency_is_bounded_and_requests_keep_http_meaning():
    """Eight in-flight requests may proceed; a ninth waits, and GET versus POST retains its native payload semantics."""
    async def scenario():
        """Hold native responses until the admission boundary has been observed."""
        release = asyncio.Event(); admitted = []; batch_ready = asyncio.Event()
        async def handle(request):
            """Count real adapter admissions without immediately completing the requests."""
            admitted.append((request.method, str(request.url), request.content))
            if len(admitted) == 8:
                batch_ready.set()
            await release.wait()
            return httpx2.Response(200, json={'native': True})
        runtime = ToolRuntime('retrieval', REFERENCE, SmallKnowledge(), httpx2.AsyncClient(transport=httpx2.MockTransport(handle)))
        tasks = [asyncio.create_task(runtime.call(runtime.FEATURE_URL, '/health/ready', {} if i == 0 else None)) for i in range(9)]
        try:
            await asyncio.wait_for(batch_ready.wait(), timeout=2)
            await asyncio.sleep(.02)
            assert len(admitted) == 8
            assert admitted[0] == ('POST', 'http://iot-feature-api.iot-features.svc.cluster.local/health/ready', b'{}')
            assert all(row[0] == 'GET' for row in admitted[1:])
            release.set()
            assert await asyncio.gather(*tasks) == [{'native': True}] * 9
            assert len(admitted) == 9
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await runtime.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('field', ['as_of', 'prepared_wall_utc', 'protocol_sha256', 'source_manifest_sha256'])
def test_drift_cohort_cannot_mix_source_snapshots(field, tmp_path):
    """Individually valid rows from different clocks or source versions must never reach the comparison backend together."""
    async def scenario():
        """Observe the real adapter's outgoing routes and reject a deliberately mixed cohort."""
        compared = []
        def handle(request):
            """Return a single mismatched source field for one selected machine."""
            if request.url.path == '/v1/drift:compare':
                compared.append(True)
                raise AssertionError('Mixed snapshots reached drift calculation')
            identifier = request.url.path.split('/')[3]
            row = machine(identifier)
            if identifier == 'P-002':
                if field == 'as_of':
                    row['as_of'] = '2026-10-01T00:05:01+00:00'; row['feature_age_seconds'] = 301.
                elif field == 'prepared_wall_utc':
                    row[field] = '2026-10-29T04:01:00+00:00'
                else:
                    row[field] = 'e' * 64
            return httpx2.Response(200, json=row)
        runtime = ToolRuntime('drift', REFERENCE, client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)))
        try:
            with pytest.raises(ToolFailure) as error:
                await runtime.drift(DriftRequest(window_id='mixed', machine_ids=[f'P-{i:03}' for i in range(1, 33)]))
            assert error.value.status == 409 and not compared
        finally:
            await runtime.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('field', ['text', 'document_id', 'revision', 'source_sha256', 'source_file', 'section'])
def test_each_knowledge_provenance_field_is_bound_to_its_chunk(field, tmp_path, monkeypatch):
    """Any returned passage identity or source field must agree with the exact retrieved chunk, even if other fields agree."""
    from types import SimpleNamespace
    row = {'chunk_id': 'c' * 64, 'document_id': 'fixture', 'revision': 1, 'source_sha256': 'd' * 64,
           'source_file': 'fixture.md', 'section': 'Section', 'text': 'Reference sentence.'}
    (tmp_path / 'chunks.json').write_text(json.dumps([row]))
    (tmp_path / 'infra/rag').mkdir(parents=True); (tmp_path / 'knowledge').mkdir()
    (tmp_path / 'infra/rag/embedding.lock.json').write_text(json.dumps({'revision': 'encoder'}))
    (tmp_path / 'knowledge/catalog.json').write_text(json.dumps({'documents': [{'document_id': 'fixture', 'sha256': 'd' * 64}]}))
    monkeypatch.setattr('rag.embeddings.TextEncoder', lambda config: SimpleNamespace(encode=lambda texts: [[1.]]))
    monkeypatch.setattr('rag.store.create_store', lambda *args: (None, None, None))
    values = {key: [value] for key, value in row.items()}; values['distance'] = [1.]
    monkeypatch.setattr('rag.retrieval.retrieve_consistent', lambda *args: (values, {}))
    reader = KnowledgeReader(tmp_path, tmp_path)
    values[field] = [2 if field == 'revision' else 'different']
    with pytest.raises(ToolFailure) as error:
        reader.search(SearchRequest(question='Explain the source', top_k=1))
    assert error.value.status == 503
