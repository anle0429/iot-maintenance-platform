"""Verify boundary behavior for the split RAG Search API."""
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

pytest.importorskip('mcp', reason='Install the dedicated MCP test dependencies')
from fastapi.testclient import TestClient
from pydantic import ValidationError

from services.rag_search.app import create_app
from services.rag_search.contracts import SearchRequest, SearchResult, read_service_token
from services.rag_search.reader import KnowledgeFailure, KnowledgeReader
from test_tools import SmallKnowledge


def test_exact_mounted_credential_boundary(tmp_path, monkeypatch):
    """Reject 31-character keys and allow the minimum 32-character configured key."""
    credential = tmp_path / 'token'
    monkeypatch.setenv('IOT_RAG_SEARCH_TOKEN_FILE', str(credential))
    credential.write_text('k' * 31)
    with pytest.raises(ValueError, match='credential'):
        read_service_token()
    credential.write_text('k' * 32 + '\n')
    assert read_service_token() == 'k' * 32
    with TestClient(create_app(reader=SmallKnowledge())) as client:
        reply = client.post('/v1/knowledge:search', json={'question': 'Read an example'},
                            headers={'Authorization': 'Bearer ' + 'k' * 32})
        assert reply.status_code == 200
        assert len(reply.json()['matches']) == 3


def test_search_match_rejects_invalid_provenance_values_and_scores():
    """Enforce source length, revision type, top-k bounds and signed finite similarity."""
    def result():
        """Return a fresh result against the one-passage contract."""
        return SmallKnowledge().search(SearchRequest(question='What is the source?', top_k=1))

    valid = result()
    valid['matches'][0].update(section='x', text='x' * 4096, cosine_similarity=-1.0)
    assert SearchResult.model_validate(valid).matches[0].cosine_similarity == -1
    for change in ({'section': ''}, {'text': 'x' * 4097}, {'revision': 0}, {'revision': '1'},
                   {'cosine_similarity': float('nan')}, {'cosine_similarity': -1.01}, {'cosine_similarity': 1.01}):
        bad = result()
        bad['matches'][0].update(change)
        with pytest.raises(ValidationError):
            SearchResult.model_validate(bad)
    for count in (0, 4):
        bad = result()
        bad['matches'] = [dict(bad['matches'][0], chunk_id=f'{index:064x}') for index in range(count)]
        with pytest.raises(ValidationError):
            SearchResult.model_validate(bad)
    bad = result()
    bad['matches'] = tuple(bad['matches'])
    with pytest.raises(ValidationError):
        SearchResult.model_validate(bad)


def test_search_ranking_accepts_float_noise_and_equal_boundary():
    """Keep small score noise valid for adjacent signed similarities."""
    request = SearchRequest(question='Find pump instructions', top_k=2)
    raw = SmallKnowledge().search(request)
    raw['matches'][0]['cosine_similarity'] = -0.20
    raw['matches'][1]['cosine_similarity'] = -0.20 + 1e-5
    assert len(SearchResult.model_validate(raw).matches) == 2


def test_search_api_rejects_extra_passages():
    """Return 503 when an upstream response violates the caller's top-k upper bound."""
    class ExcessPassages:
        """Simulate a reader returning an otherwise valid but unrequested extra passage."""

        def search(self, request):
            """Respond with two valid chunks in place of the one requested."""
            return SmallKnowledge().search(SearchRequest(question=request.question, top_k=2))

    token = 'x' * 32
    with TestClient(create_app(ExcessPassages(), token)) as client:
        result = client.post('/v1/knowledge:search', json={'question': 'Find a reference', 'top_k': 1},
                             headers={'Authorization': 'Bearer ' + token})
        assert result.status_code == 503


def test_reader_binds_catalog_unique_chunks_and_encoder_errors(tmp_path, monkeypatch):
    """Reject a changed or duplicated source and map the encoder's token limit to 422."""
    match = SmallKnowledge().search(SearchRequest(question='Find a reference', top_k=1))['matches'][0]
    row = {key: value for key, value in match.items() if key != 'cosine_similarity'}
    chunks = tmp_path / 'chunks.json'
    lock = tmp_path / 'infra/rag/embedding.lock.json'
    catalog = tmp_path / 'knowledge/catalog.json'
    lock.parent.mkdir(parents=True)
    catalog.parent.mkdir()
    lock.write_text(json.dumps({'revision': 'fixture-encoder'}))
    catalog.write_text(json.dumps({'documents': [{'document_id': row['document_id'], 'sha256': '7' * 64}]}))
    monkeypatch.setattr('rag.store.create_store', lambda *args: (None, None, None))

    def limit(_):
        """Model the real tokenizer rejecting an over-budget prompt."""
        raise ValueError('token budget exceeded')

    monkeypatch.setattr('rag.embeddings.TextEncoder', lambda _: SimpleNamespace(encode=limit))
    for digest in ('0' * 64, 'f' * 64):
        chunks.write_text(json.dumps([dict(row, source_sha256=digest)]))
        with pytest.raises(ValueError, match='source catalog'):
            KnowledgeReader(tmp_path, tmp_path)
    row['source_sha256'] = '7' * 64
    chunks.write_text(json.dumps([row, deepcopy(row)]))
    with pytest.raises(ValueError, match='identities'):
        KnowledgeReader(tmp_path, tmp_path)
    chunks.write_text(json.dumps([row]))
    reader = KnowledgeReader(tmp_path, tmp_path)
    assert reader.snapshot == sha256(chunks.read_bytes()).hexdigest()
    with pytest.raises(KnowledgeFailure) as error:
        reader.search(SearchRequest(question='A valid question'))
    assert error.value.status == 422
