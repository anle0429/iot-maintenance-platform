"""Build an isolated Feast feature view with native Milvus vector indexing for versioned knowledge chunks."""
from datetime import timedelta
from pathlib import Path
import json

FEATURES = ['text', 'document_id', 'revision', 'source_sha256', 'source_file', 'section', 'embedding_revision']


def create_store(directory, config):
    """Bind a new RAG project to its own registry, Parquet source and Milvus Lite file, preserving ML stores."""
    from feast import Entity, FeatureStore, FeatureView, Field, FileSource
    from feast.repo_config import RepoConfig
    from feast.types import Array, Float32, Int64, String, ValueType
    directory = Path(directory).resolve()
    repository = RepoConfig(project='iot_knowledge', provider='local', registry=str(directory / 'registry.db'),
        offline_store={'type': 'file'}, online_store={'type': 'milvus', 'path': str(directory / 'knowledge.db'),
        'vector_enabled': True, 'embedding_dim': config['dimensions'], 'index_type': 'FLAT', 'metric_type': 'COSINE',
        'index_params': {}, 'search_params': {}, 'consistency_level': 'Strong', 'collection_consistency_level': 'Strong'},
        entity_key_serialization_version=3, auth={'type': 'no_auth'})
    entity = Entity(name='chunk', join_keys=['chunk_id'], value_type=ValueType.STRING)
    source = FileSource(name='knowledge_source', path=str(directory / 'chunks.parquet'), timestamp_field='event_timestamp', created_timestamp_column='created')
    fields = [Field(name=name, dtype=String) for name in FEATURES if name != 'revision'] + [Field(name='revision', dtype=Int64),
              Field(name='embedding', dtype=Array(Float32), vector_index=True, vector_search_metric='COSINE')]
    view = FeatureView(name='knowledge_chunks', entities=[entity], ttl=timedelta(days=config['knowledge_ttl_days']), schema=fields, source=source, online=True)
    return FeatureStore(config=repository), entity, view


def retrieve(store, vector, top_k=3):
    """Use Feast's actual vector-retrieval API instead of a separate hand-written search engine."""
    if not 1 <= top_k <= 10: raise ValueError('top_k must be between 1 and 10')
    columns = ['knowledge_chunks:' + name for name in ['embedding'] + FEATURES]
    return store.retrieve_online_documents_v2(features=columns, query=vector, top_k=top_k, distance_metric='COSINE').to_dict()
