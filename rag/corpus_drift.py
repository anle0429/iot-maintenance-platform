"""Compare complete RAG corpus versions with document-weighted Gaussian MMD before publication."""
from hashlib import sha256
from pathlib import Path
import argparse
import json
import math
from rag.corpus import chunk_documents, read_catalog


def fingerprint(value):
    """Hash deterministic JSON, rejecting NaN and infinity in version identities."""
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_vectors(vectors, dimensions):
    """Require a small, nonempty corpus of finite normalized document vectors."""
    if not 1 <= len(vectors) <= 128 or not 1 <= dimensions <= 4096:
        raise ValueError('Bounded corpus and vector dimensions are required')
    for vector in vectors:
        if (len(vector) != dimensions or any(isinstance(v, bool) or not isinstance(v, (float, int))
                                           or not math.isfinite(v) for v in vector)
                or abs(math.fsum(v*v for v in vector)-1) > .001):
            raise ValueError('Document vectors must be finite, dimension-matched and normalized')


def mmd_squared(left, right, sigma):
    """Return the squared empirical RKHS distance, including diagonal terms; this is not a p-value."""
    if isinstance(sigma, bool) or not isinstance(sigma, (float, int)) or not math.isfinite(sigma) or sigma <= 0:
        raise ValueError('A positive finite Gaussian bandwidth is required')
    dimensions = len(left[0]) if left else 0
    validate_vectors(left, dimensions); validate_vectors(right, dimensions)
    def kernel(a, b):
        """Measure Gaussian similarity between two validated document vectors."""
        return math.exp(-math.fsum((x-y)**2 for x, y in zip(a, b, strict=True))/(2*sigma*sigma))
    xx = math.fsum(kernel(a, b) for a in left for b in left)/(len(left)**2)
    yy = math.fsum(kernel(a, b) for a in right for b in right)/(len(right)**2)
    xy = math.fsum(kernel(a, b) for a in left for b in right)/(len(left)*len(right))
    value = xx+yy-2*xy
    if value < -1e-12: raise ArithmeticError('Negative empirical squared norm exceeds cancellation tolerance')
    return max(0., value)


def document_vectors(documents, config, encoder):
    """Aggregate real chunk embeddings into one unit centroid per document, with equal document weights."""
    identifiers = [d['document_id'] for d in documents]
    if not 1 <= len(identifiers) <= 128 or len(set(identifiers)) != len(identifiers):
        raise ValueError('Unique bounded document identities are required')
    documents = sorted(documents, key=lambda row: row['document_id'])
    chunks = chunk_documents(documents, config['chunk_max_chars'], config['overlap_chars'])
    if len(chunks) > 4096: raise ValueError('The embedding workload exceeds this operator gate budget')
    vectors = encoder.encode([row['text'] for row in chunks])
    if len(vectors) != len(chunks): raise ValueError('Every chunk must have exactly one embedding')
    validate_vectors_batches(vectors, config['dimensions'])
    result = []
    for document in documents:
        selected = [vector for chunk, vector in zip(chunks, vectors, strict=True)
                    if chunk['document_id'] == document['document_id']]
        if not selected: raise ValueError('Every document must contain an embeddable section')
        mean = [math.fsum(v[i] for v in selected)/len(selected) for i in range(config['dimensions'])]
        norm = math.sqrt(math.fsum(v*v for v in mean))
        if norm < 1e-12: raise ValueError('A zero centroid cannot be normalized')
        result.append({'document_id': document['document_id'], 'source_sha256': document['sha256'],
                       'chunk_count': len(selected), 'vector': [v/norm for v in mean]})
    return {'documents': result, 'chunks': chunks, 'chunk_vectors': vectors}


def validate_vectors_batches(vectors, dimensions):
    """Check bounded chunk batches without treating chunks as independent document observations."""
    for offset in range(0, len(vectors), 128): validate_vectors(vectors[offset:offset+128], dimensions)


def decide(reference, candidate, protocol):
    """Flag version-level distance above a predeclared review tolerance; make no accuracy or safety claim."""
    tolerance = protocol['review_mmd_squared']
    if (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
            or not math.isfinite(tolerance) or not 0 < tolerance < 2):
        raise ValueError('A finite review tolerance in (0,2) is required')
    value = mmd_squared([d['vector'] for d in reference['documents']],
                        [d['vector'] for d in candidate['documents']], protocol['sigma'])
    return {'mmd_squared': value, 'review_mmd_squared': tolerance, 'sigma': protocol['sigma'],
            'requires_review': value > tolerance, 'allowed': value <= tolerance,
            'reference_documents': len(reference['documents']), 'candidate_documents': len(candidate['documents']),
            'reference_fingerprint': fingerprint(reference), 'candidate_fingerprint': fingerprint(candidate),
            'interpretation': 'Complete finite-corpus version distance; not a hypothesis test, accuracy metric or safety certificate.'}


def scenario_documents(documents, scenario):
    """Create explicit read-only controls; counterfactual documents never enter the production store."""
    if scenario == 'production': return documents
    if scenario == 'reordered': return list(reversed(documents))
    if scenario == 'whitespace':
        return [dict(d, text=d['text']+'\n\n', sha256=sha256((d['text']+'\n\n').encode()).hexdigest()) for d in documents]
    if scenario != 'out-of-domain': raise ValueError('Unknown corpus control')
    texts = [
        '# Card payments\nA card transaction records merchant category, amount, currency and authorization outcome. Chargeback review uses transaction history.\n',
        '# Retail inventory\nA retail warehouse tracks products, shelf quantity and replenishment orders. Stockouts and sales forecasts guide store deliveries.\n',
        '# Hotel reservations\nA hotel booking records arrival date, departure date, guest count and cancellation policy. Room availability determines the nightly quote.\n',
        '# Recipe catalog\nA recipe lists ingredients, cooking temperature and preparation time. Baking bread requires flour, water, yeast and a proofing interval.\n']
    if len(documents) != 4: raise ValueError('This declared counterfactual control requires exactly four documents')
    return [dict(d, text=text, sha256=sha256(text.encode()).hexdigest()) for d, text in zip(documents, texts, strict=True)]


def evaluate(source, reference_path, protocol_path, scenarios):
    """Authenticate pinned source/model/protocol bytes before loading the actual cached encoder."""
    from rag.embeddings import TextEncoder
    source = Path(source); reference_path = Path(reference_path); protocol_path = Path(protocol_path)
    protocol = json.loads(protocol_path.read_bytes()); reference_raw = reference_path.read_bytes()
    config_raw = (source/'infra/rag/embedding.lock.json').read_bytes(); config = json.loads(config_raw)
    if (protocol.get('version') != 1 or protocol.get('unit') != 'normalized-document-centroid'
            or sha256(reference_raw).hexdigest() != protocol['reference_sha256']
            or sha256(config_raw).hexdigest() != protocol['embedding_config_sha256']):
        raise ValueError('The reference, vector profile or distance protocol changed')
    baseline = json.loads(reference_raw)['documents']
    for document in baseline:
        if sha256(document['text'].encode()).hexdigest() != document['sha256']:
            raise ValueError('The frozen reference text differs from its source identity')
    documents = read_catalog(source/'knowledge'); encoder = TextEncoder(config)
    reference = document_vectors(baseline, config, encoder)
    candidates = {}
    for scenario in scenarios:
        texts = scenario_documents(documents, scenario); candidate = document_vectors(texts, config, encoder)
        candidates[scenario] = {'source_documents': texts, 'embeddings': candidate, 'decision': decide(reference, candidate, protocol)}
    return {'schema_version': 1, 'protocol': protocol, 'protocol_sha256': sha256(protocol_path.read_bytes()).hexdigest(),
            'embedding_config': config, 'reference': reference, 'candidates': candidates,
            'production_catalog_sha256': sha256((source/'knowledge/catalog.json').read_bytes()).hexdigest()}


def main():
    """Write original vectors and decisions once, and fail a selected denied candidate before any publication."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True); parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenario', choices=['production', 'out-of-domain', 'suite'], default='production')
    args = parser.parse_args()
    scenarios = ['production', 'reordered', 'whitespace', 'out-of-domain'] if args.scenario == 'suite' else [args.scenario]
    report = evaluate(args.source, args.reference, args.protocol, scenarios)
    with args.output.open('x', encoding='utf-8') as stream: json.dump(report, stream, indent=2, allow_nan=False); stream.write('\n')
    decisions = {name: value['decision'] for name, value in report['candidates'].items()}
    print(json.dumps(decisions, allow_nan=False))
    if args.scenario != 'suite' and not decisions[args.scenario]['allowed']: raise SystemExit(2)


if __name__ == '__main__': main()
