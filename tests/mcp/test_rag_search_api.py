"""Exercise the private RAG service and both public retrieval protocols across their HTTP boundary."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
import asyncio
import json

import pytest

pytest.importorskip('mcp', reason='Install the dedicated MCP test dependencies')
import httpx2
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from services.mcp_tools.app import create_app as create_mcp_app
from services.mcp_tools.contracts import MachineRequest
from services.mcp_tools.runtime import ToolRuntime, ToolFailure
from services.rag_search.app import create_app as create_search_app
from services.rag_search.contracts import SearchRequest, SearchResult
from services.rag_search.reader import KnowledgeFailure
from test_tools import HEADERS, RAG_TOKEN, REFERENCE, TOKEN, SmallKnowledge, machine


def test_mcp_and_rest_use_the_private_search_api(tmp_path, monkeypatch):
    """Forward bounded search through HTTP, using the backend's credential only for its search route."""
    token_path = tmp_path / 'service-token'
    token_path.write_text(RAG_TOKEN)
    monkeypatch.setenv('IOT_RAG_SEARCH_TOKEN_FILE', str(token_path))
    monkeypatch.setenv('IOT_RAG_SEARCH_URL', 'http://private-search.svc.cluster.local')
    queries = []
    with TestClient(create_search_app(reader=SmallKnowledge())) as search:
        def forward(request):
            """Deliver the adapter's real HTTP request to the separate application's router."""
            if request.url.host == 'iot-feature-api.iot-features.svc.cluster.local':
                assert 'authorization' not in request.headers
                return httpx2.Response(200, json={'ready': True} if request.url.path == '/health/ready' else machine())
            assert request.url.host == 'private-search.svc.cluster.local'
            if request.url.path == '/v1/knowledge:search':
                queries.append(json.loads(request.content))
                assert request.headers['authorization'] == 'Bearer ' + RAG_TOKEN
            else:
                assert request.url.path == '/health/ready' and 'authorization' not in request.headers
            result = search.request(request.method, request.url.path, content=request.content, headers=dict(request.headers))
            return httpx2.Response(result.status_code, content=result.content)

        body = {'question': 'What does a stopped pump mean?', 'top_k': 2}
        assert search.get('/health/live').json() == {'live': True, 'service': 'rag-search'}
        assert search.post('/v1/knowledge:search', json=body, headers=HEADERS).status_code == 401
        search_headers = {'Authorization': 'Bearer ' + RAG_TOKEN}
        assert search.post('/v1/knowledge:search', json=dict(body, top_k=4), headers=search_headers).status_code == 422
        expected = search.post('/v1/knowledge:search', json=body, headers=search_headers).json()
        runtime = ToolRuntime('retrieval', REFERENCE, client=httpx2.AsyncClient(transport=httpx2.MockTransport(forward)))
        with TestClient(create_mcp_app(runtime, 'retrieval', TOKEN)) as front:
            assert front.get('/health/ready').json()['ready']
            assert front.get('/health/dependencies', headers=HEADERS).json()['ready']
            assert front.get('/v1/machines/P-001/features', headers=HEADERS).status_code == 200
            assert front.post('/v1/knowledge:search', json=body, headers=HEADERS).json() == expected
            response = front.post('/mcp', headers={**HEADERS, 'Accept': 'application/json, text/event-stream'},
                                  json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                                      'name': 'search_knowledge', 'arguments': {'request': body}}})
            record = response.json()['result']
            assert not record['isError'] and json.loads(record['content'][0]['text']) == expected
    assert queries == [body, body]


@pytest.mark.parametrize('unavailable', ['feature', 'search'])
def test_retrieval_readiness_survives_either_dependency_outage(unavailable):
    """A fresh front door still serves the healthy tool and reports dependencies separately."""
    active_outage = unavailable
    def forward(request):
        """Return a scoped outage or a valid response from the other upstream."""
        backend = 'feature' if request.url.host.startswith('iot-feature-api.') else 'search'
        if backend == active_outage:
            return httpx2.Response(503, text='private backend details')
        if request.url.path == '/health/ready':
            value = {'ready': True, 'service': 'rag-search'} if backend == 'search' else {'ready': active_outage != 'feature_unready'}
            return httpx2.Response(200, json=value)
        if backend == 'feature':
            return httpx2.Response(200, json=machine())
        if active_outage == 'search_busy':
            return httpx2.Response(429)
        return httpx2.Response(200, json=SmallKnowledge().search(SearchRequest.model_validate_json(request.content)))

    runtime = ToolRuntime('retrieval', REFERENCE,
                          client=httpx2.AsyncClient(transport=httpx2.MockTransport(forward)), rag_token=RAG_TOKEN)
    with TestClient(create_mcp_app(runtime, 'retrieval', TOKEN)) as api:
        assert api.get('/health/ready').status_code == 200
        assert api.get('/health/dependencies').status_code == 401
        strict = api.get('/health/dependencies', headers=HEADERS)
        assert strict.status_code == 503 and 'private backend' not in strict.text
        features = api.get('/v1/machines/P-001/features', headers=HEADERS)
        body = {'question': 'What does a stopped pump mean?', 'top_k': 2}
        search = api.post('/v1/knowledge:search', json=body, headers=HEADERS)
        assert (features.status_code, search.status_code) == ((503, 200) if unavailable == 'feature' else (200, 503))
        tool, request = ('get_machine_features', {'machine_id': 'P-001'}) if unavailable == 'feature' else ('search_knowledge', body)
        native = api.post('/mcp', headers={**HEADERS, 'Accept': 'application/json, text/event-stream'},
                          json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                              'name': tool, 'arguments': {'request': request}}}).json()['result']
        assert native['isError'] is True and '503:' in native['content'][0]['text']
        active_outage = None
        assert api.get('/health/dependencies', headers=HEADERS).json()['ready'] is True
        active_outage = 'feature_unready'
        assert api.get('/health/dependencies', headers=HEADERS).status_code == 503
        active_outage = 'search_busy'
        overloaded = api.post('/v1/knowledge:search', json=body, headers=HEADERS)
        assert overloaded.status_code == 429 and overloaded.headers['retry-after'] == '1'


def test_search_bounds_concurrent_admission_and_counts_offered_work():
    """At saturation the bounded queue can expire and additional work is rejected before reaching the reader."""
    entered, release = Event(), Event()

    class BlockingReader:
        """Hold one native search until the queue and rejection are observed at the HTTP boundary."""

        def search(self, request):
            """Keep the worker occupied and return an otherwise valid snapshot result."""
            entered.set()
            assert release.wait(5)
            return SmallKnowledge().search(request)

    def fetch_metrics(api):
        """Read samples from the real Prometheus endpoint, excluding client-generated creation timestamps."""
        return {sample.name if not sample.labels else (sample.name, sample.labels['result']): sample.value
                for family in text_string_to_metric_families(api.get('/metrics').text)
                for sample in family.samples if not sample.name.endswith('_created')}

    headers = {'Authorization': 'Bearer ' + RAG_TOKEN}
    body = {'question': 'What does a stopped pump mean?', 'top_k': 2}
    with TestClient(create_search_app(reader=BlockingReader(), token=RAG_TOKEN)) as api, ThreadPoolExecutor(3) as pool:
        assert api.post('/v1/knowledge:search', json=body, headers=HEADERS).status_code == 401
        assert api.post('/v1/knowledge:search', json=dict(body, top_k=4), headers=headers).status_code == 422
        first = pool.submit(api.post, '/v1/knowledge:search', json=body, headers=headers)
        try:
            assert entered.wait(3)
            waiting = pool.submit(api.post, '/v1/knowledge:search', json=body, headers=headers)
            deadline = monotonic() + 0.5
            while fetch_metrics(api).get('iot_rag_search_queued', 0) != 1 and monotonic() < deadline:
                sleep(.01)
            assert fetch_metrics(api)['iot_rag_search_queued'] == 1
            overflow = api.post('/v1/knowledge:search', json=body, headers=headers)
            assert overflow.status_code == 429 and overflow.headers['retry-after'] == '1'
            expired = waiting.result(timeout=3)
            assert expired.status_code == 429 and expired.headers['retry-after'] == '1'
            assert not first.done()
        finally:
            release.set()
        assert first.result(timeout=3).status_code == 200
        assert api.post('/v1/knowledge:search', json=body, headers=headers).status_code == 200
        metrics = fetch_metrics(api)
        assert metrics['iot_rag_search_attempts_total'] == 4
        assert metrics['iot_rag_search_active'] == metrics['iot_rag_search_queued'] == 0
        assert metrics[('iot_rag_search_outcomes_total', 'success')] == 2
        assert metrics[('iot_rag_search_outcomes_total', 'busy')] == 2


def test_saturated_search_cannot_queue_unbounded_work_or_starve_machine_reads():
    """Use distinct front-door limits for native requests with independent upstreams."""
    async def check():
        """Hold every upstream Search slot while exercising actual feature parsing and overload."""
        entered, release = asyncio.Event(), asyncio.Event()
        attempts = 0

        async def forward(request):
            """Block every Search call while the Feature API continues to respond."""
            nonlocal attempts
            if request.url.path == '/v1/knowledge:search':
                attempts += 1
                if attempts == 8:
                    entered.set()
                await release.wait()
                return httpx2.Response(429)
            return httpx2.Response(200, json=machine())

        runtime = ToolRuntime('retrieval', REFERENCE,
                              client=httpx2.AsyncClient(transport=httpx2.MockTransport(forward)), rag_token=RAG_TOKEN)
        request = SearchRequest(question='What does a stopped pump mean?')
        tasks = [asyncio.create_task(runtime.search(request)) for _ in range(8)]
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            with pytest.raises(ToolFailure) as busy:
                await asyncio.wait_for(runtime.search(request), timeout=1)
            assert busy.value.status == 429 and attempts == 8
            feature = await asyncio.wait_for(runtime.features(MachineRequest(machine_id='P-001')), timeout=1)
            assert feature['machine_id'] == 'P-001'
        finally:
            release.set()
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            await runtime.close()
        assert all(isinstance(result, ToolFailure) and result.status == 429 for result in outcomes)

    asyncio.run(check())


@pytest.mark.parametrize('failure,code', [('limit', 422), ('backend', 503), ('incomplete', 503), ('duplicate', 503)])
def test_private_api_hides_diagnostics_and_rejects_partial_results(failure, code):
    """A failed, incomplete or invalid search cannot become a successful tool response."""
    class UnavailableKnowledge:
        """Return one controlled invalid search result or raise a native dependency error."""

        def search(self, request):
            """Simulate a safe token-limit rejection, private exception or invalid ranking."""
            if failure == 'limit':
                raise KnowledgeFailure(422, 'Question exceeds the embedding model token limit')
            if failure == 'backend':
                raise RuntimeError('confidential local filesystem path')
            result = SmallKnowledge().search(request)
            if failure == 'incomplete':
                result['matches'].pop()
            else:
                result['matches'][1] = result['matches'][0]
            return result

    with TestClient(create_search_app(reader=UnavailableKnowledge(), token=RAG_TOKEN)) as api:
        response = api.post('/v1/knowledge:search', json={'question': 'Read a reference', 'top_k': 2},
                            headers={'Authorization': 'Bearer ' + RAG_TOKEN})
        assert response.status_code == code and 'confidential' not in response.text


def test_snapshot_must_open_before_serving(monkeypatch):
    """An unreadable mounted snapshot stops startup before the service can report ready."""
    def unavailable(state, source_root):
        """Exercise the production paths without opening a real Milvus process in a unit test."""
        assert (state.as_posix(), source_root) == ('/state/snapshot', '/workspace')
        raise ValueError('index unavailable')

    monkeypatch.setattr('services.rag_search.app.KnowledgeReader', unavailable)
    with pytest.raises(ValueError, match='index unavailable'), TestClient(create_search_app(token=RAG_TOKEN)):
        pytest.fail('A process without a loaded snapshot accepted traffic')


def test_uninitialized_replica_and_weak_service_credentials(tmp_path, monkeypatch):
    """Health checks and service startup must fail safely when a required local binding is absent."""
    with pytest.raises(ValueError, match='credential'):
        create_search_app(token='weak')
    token_file = tmp_path / 'token'
    token_file.write_text('weak')
    monkeypatch.setenv('IOT_RAG_SEARCH_TOKEN_FILE', str(token_file))
    with pytest.raises(ValueError, match='credential'):
        create_search_app()
    client = TestClient(create_search_app(reader=SmallKnowledge(), token=RAG_TOKEN))
    try:
        assert client.get('/health/ready').status_code == 503
        assert client.post('/v1/knowledge:search', headers={'Authorization': 'Bearer ' + RAG_TOKEN},
                           json={'question': 'Check uninitialized search'}).status_code == 503
    finally:
        client.close()
    runtime = ToolRuntime('retrieval', REFERENCE, client=object(), rag_token=RAG_TOKEN)
    front = TestClient(create_mcp_app(runtime, 'retrieval', TOKEN))
    try:
        assert front.get('/health/ready').status_code == 503
        assert front.get('/health/dependencies', headers=HEADERS).status_code == 503
    finally:
        front.close()


def test_reversed_passages_are_not_a_valid_ranking():
    """A reply cannot silently change native retrieval order while keeping the same unique chunk IDs."""
    result = SmallKnowledge().search(SearchRequest(question='What does this mean?', top_k=2))
    result['matches'].reverse()
    with pytest.raises(ValueError, match='ranking'):
        SearchResult.model_validate(result)
