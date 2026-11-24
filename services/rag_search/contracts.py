"""Shared, bounded wire contracts between Retrieval MCP and the knowledge search service."""
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


Digest = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]
SourceField = Annotated[str, StringConstraints(min_length=1, max_length=4096)]


def read_service_token():
    """Read the dedicated service-to-service bearer credential from its mounted secret."""
    token = Path(os.environ['IOT_RAG_SEARCH_TOKEN_FILE']).read_text().strip()
    if len(token) < 32:
        raise ValueError('A scoped RAG search credential is required')
    return token


class SearchRequest(BaseModel):
    """Bound retrieval context before encoding or querying the native feature store."""
    model_config = ConfigDict(extra='forbid', strict=True)
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=1000)]
    top_k: Annotated[int, Field(ge=1, le=3)] = 3


class SearchMatch(BaseModel):
    """Return just the native ranked passage and its source identity."""
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    chunk_id: Digest
    document_id: SourceField
    revision: Annotated[int, Field(ge=1)]
    source_sha256: Digest
    source_file: SourceField
    section: SourceField
    text: SourceField
    cosine_similarity: Annotated[float, Field(ge=-1.00001, le=1.00001)]


class SearchResult(BaseModel):
    """Bind unique ranked results to the snapshot and pinned encoder revision."""
    model_config = ConfigDict(extra='forbid', strict=True)
    matches: Annotated[list[SearchMatch], Field(min_length=1, max_length=3)]
    snapshot_sha256: Digest
    embedding_revision: Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{40}$')]
    source_type: Literal['authored-synthetic-reference']

    @model_validator(mode='after')
    def ranked_unique_matches(self):
        """Reject duplicate chunks or reversed native ranking beyond numeric tolerance."""
        if len({match.chunk_id for match in self.matches}) != len(self.matches):
            raise ValueError('Duplicate knowledge passage')
        if any(left.cosine_similarity + 1e-5 < right.cosine_similarity
               for left, right in zip(self.matches, self.matches[1:])):
            raise ValueError('Invalid knowledge ranking')
        return self
