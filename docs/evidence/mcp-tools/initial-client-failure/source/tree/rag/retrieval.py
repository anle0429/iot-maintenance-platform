"""Rehydrate ranked Feast entity keys individually so every returned passage retains the correct provenance."""
import math
from rag.store import retrieve, FEATURES


def retrieve_consistent(store, vector, top_k=3):
    """Preserve native vector-search keys, then use singleton SDK reads to avoid cross-column ordering ambiguity."""
    raw = retrieve(store, vector, top_k)
    identifiers = raw.get('chunk_id', [])
    if len(identifiers) != top_k or len(set(identifiers)) != top_k:
        raise ValueError('Native vector retrieval returned missing or duplicate identities')
    if not vector or any(not math.isfinite(value) for value in vector): raise ValueError('Invalid query vector')
    columns = FEATURES + ['embedding']
    result = {name: [] for name in ['chunk_id'] + columns + ['distance']}
    reads = []
    for identifier in identifiers:
        row = store.get_online_features(features=['knowledge_chunks:' + name for name in columns],
                                        entity_rows=[{'chunk_id': identifier}]).to_dict()
        if row.get('chunk_id') != [identifier] or any(len(row.get(name, [])) != 1 for name in columns):
            raise ValueError('Singleton feature read did not preserve the requested chunk identity')
        embedding = row['embedding'][0]
        if len(embedding) != len(vector) or any(not math.isfinite(value) for value in embedding):
            raise ValueError('Invalid stored embedding')
        divisor = math.sqrt(sum(value*value for value in vector) * sum(value*value for value in embedding))
        if divisor == 0: raise ValueError('A zero vector has no cosine similarity')
        score = sum(a*b for a, b in zip(vector, embedding)) / divisor
        for name in ['chunk_id'] + columns: result[name].append(row[name][0])
        result['distance'].append(score)
        reads.append(row)
    if any(left + 1e-5 < right for left, right in zip(result['distance'], result['distance'][1:])):
        raise ValueError('Native ranked identities disagree with their individually read vectors')
    trace = {'native_search': raw, 'singleton_feature_reads': reads,
             'contract': 'Native ranked entity IDs; independently keyed Feast feature reads; cosine recomputed from those vectors.'}
    return result, trace
