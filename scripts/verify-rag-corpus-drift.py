"""Verify genuine corpus embeddings, independently recomputed distance and actual CI publication boundaries offline."""
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import json
import math
import sys
import tarfile
from integration_evidence import ROOT, capsule, read
from evidence_integrity import documentation_equivalent
sys.path.insert(0, str(ROOT))
from rag.corpus import chunk_documents
from rag.corpus_drift import fingerprint
from rag.drift_ci import require_distance_receipt
from agents.ci_contract import release_identity
MANIFEST = 'afcddab5dcf0965a3adc7497c2d222e40d549574d6acc99434b70e60c692565e'
COMMIT = 'a4a69138740113c291a1f6bfe23c3705014a884b'
DOC_ONLY = {
    'rag/corpus_drift.py': ('79c32c7314ff218b8f40e3cfb8042e9ce2b3a2f5e82e43298cfeed373d375227',
                          '7652168c0f49451fee8bb40cb9593b1b2b7ddd3ccfdd6451ae174340684ebc5e'),
    'tests/test_rag_corpus_drift.py': ('4630d44993bfa04536787e01ab53c9c74dfc873e9d7695e146e5ffab429f0aa4',
                                    'fc15e257a453da446ad9280fc2669dd90d63f3cfa2e53982af3a648de017b194'),
}


def independent_distance(left, right, sigma):
    """Recompute the squared norm from cross-pairs, separately from the production implementation."""
    def average(a, b):
        """Compute all pairwise kernel similarities without the production arithmetic helper."""
        return sum(math.exp(-sum((x-y)**2 for x,y in zip(u,v,strict=True))/(2*sigma**2))
                   for u in a for v in b)/(len(a)*len(b))
    return max(0., average(left,left)+average(right,right)-2*average(left,right))


def verify():
    """Authenticate original source/native bytes and distinguish an expected denial from a failed deployment."""
    base, count = capsule('rag-corpus-drift', MANIFEST); assert count == 9
    meta = read(base,'native-archive.json'); raw = (base/'native-receipts.tar.gz').read_bytes()
    assert len(raw) == meta['archive_bytes'] == 1229497
    assert sha256(raw).hexdigest() == meta['archive_sha256'] == 'a5ab8e41f4214515d80dfbd4e8ce4a9c48d14b7429308fd612f1bd758bc07e5e'
    assert meta['current_native_credentials_scanned'] and meta['vault_root_and_unseal_scanned'] and meta['pc_actual_gpu_join_credential_scanned']
    assert not meta['recompressed_or_rewritten_on_pc'] and not meta['workloads_rerun_for_capture']
    bodies = {}
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            if not member.isfile(): assert member.isdir(); continue
            name = PurePosixPath(member.name).relative_to('rag-drift-capture-30a')
            assert not name.is_absolute() and '..' not in name.parts and member.size < 10000000
            assert name.as_posix() not in bodies
            bodies[name.as_posix()] = archive.extractfile(member).read()
    def native(name):
        """Decode one authenticated original native JSON payload."""
        return json.loads(bodies[name])
    inventory = native('manifest.json')
    assert sha256(bodies['manifest.json']).hexdigest() == meta['original_inventory_sha256'] == '8b1901a623060e9cebc76712d83fe6b3901137240daabbdb015d08ccc67cefcb'
    assert len(inventory['files']) == meta['native_payloads'] == 161
    assert set(bodies) == set(inventory['files']) | {'manifest.json'}
    for name,pin in inventory['files'].items():
        assert len(bodies[name]) == pin['bytes'] and sha256(bodies[name]).hexdigest() == pin['sha256']
    seal = native('source/manifest.json'); assert seal['commit'] == COMMIT and len(seal['files']) == 50
    for name,digest in seal['files'].items():
        original = bodies['source/tree/'+name]; current = (ROOT/name).read_bytes()
        assert sha256(original).hexdigest() == digest
        normalized = current if current == original else current.replace(b'\r\n',b'\n')
        if name in DOC_ONLY:
            assert (sha256(original).hexdigest(),sha256(normalized).hexdigest()) == DOC_ONLY[name]
            assert documentation_equivalent(original,normalized,'.py'), name
        else: assert normalized == original, name
    source_digest = sha256(json.dumps(seal,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    suite = native('operator/suite/distance-report.json')
    protocol = native('source/tree/infra/rag/corpus-drift-protocol.json')
    assert protocol['sigma'] == 1. and protocol['review_mmd_squared'] == .02
    assert sha256(bodies['source/tree/infra/rag/corpus-reference.json']).hexdigest() == protocol['reference_sha256']
    assert sha256(bodies['source/tree/infra/rag/embedding.lock.json']).hexdigest() == protocol['embedding_config_sha256']
    assert suite['protocol'] == protocol and suite['embedding_config']['dimensions'] == 384
    assert suite['embedding_config']['revision'] == '1110a243fdf4706b3f48f1d95db1a4f5529b4d41'
    assert set(suite['candidates']) == {'production','reordered','whitespace','out-of-domain'}
    errors = []; scores = {}
    reports = [suite,native('release/4/distance-report.json'),native('release/5/distance-report.json')]
    for report in reports:
        assert report['protocol'] == protocol
        assert report['protocol_sha256'] == seal['files']['infra/rag/corpus-drift-protocol.json']
        assert report['embedding_config'] == native('source/tree/infra/rag/embedding.lock.json')
        reference = report['reference']; assert len(reference['documents']) == 4
        for scenario,candidate in report['candidates'].items():
            embedded = candidate['embeddings']; source = candidate['source_documents']; assert len(source) == 4
            assert chunk_documents(sorted(source,key=lambda d:d['document_id']),1100,160) == embedded['chunks']
            for document in source: assert sha256(document['text'].encode()).hexdigest() == document['sha256']
            for population in [reference,embedded]:
                assert len(population['documents']) == 4
                for doc in population['documents']:
                    vectors = [v for chunk,v in zip(population['chunks'],population['chunk_vectors'],strict=True) if chunk['document_id'] == doc['document_id']]
                    assert len(vectors) == doc['chunk_count'] > 0
                    for v in vectors:
                        assert len(v) == 384 and all(math.isfinite(x) for x in v) and abs(sum(x*x for x in v)-1) < .001
                    mean = [sum(v[i] for v in vectors)/len(vectors) for i in range(384)]; norm = math.sqrt(sum(x*x for x in mean))
                    errors.append(max(abs(a-b/norm) for a,b in zip(doc['vector'],mean,strict=True)))
            decision = candidate['decision']
            actual = independent_distance([d['vector'] for d in reference['documents']], [d['vector'] for d in embedded['documents']],1)
            errors.append(abs(actual-decision['mmd_squared']))
            assert decision['allowed'] == (actual <= .02) and decision['requires_review'] == (actual > .02)
            assert decision['reference_fingerprint'] == fingerprint(reference) and decision['candidate_fingerprint'] == fingerprint(embedded)
            scores[scenario] = actual
    assert max(errors) < 1e-12
    assert scores['production'] == scores['reordered'] == scores['whitespace'] == 0
    assert abs(scores['out-of-domain']-.4375155223734153) < 1e-12
    before4 = native('operator/4-before.json'); before5 = native('operator/5-before.json')
    assert all(before4[k] == before5[k] for k in ['dag_sha256','releases','run_directories'])
    assert native('operator/install-recheck.json')['unrelated_job_fields_preserved']
    denied_console = bodies['jenkins/4/console.txt'].decode()
    for stage in ['Auto deploy the sealed six-task DAG','Run native Feast RAG validation and DataHub readback']:
        assert 'Stage "'+stage+'" skipped due to earlier failure(s)' in denied_console
    for name in ['corpus-distance.json','deploy.attempt','admitted-deploy-host.attempt','deploy.json','verify.json']:
        assert 'release/4/'+name not in bodies
    for number,status,exit_code in [(4,'FAILURE',2),(5,'SUCCESS',0)]:
        build = native('jenkins/'+str(number)+'/build.json'); assert build['result'] == status and not build['building']
        gate = native('release/'+str(number)+'/corpus-distance-container.json')
        packaged = native('release/'+str(number)+'/build.json')
        assert gate['image'] == packaged['image_id'] and gate['state']['ExitCode'] == exit_code and not gate['state']['OOMKilled']
        assert not gate['state']['Running'] and gate['user'] == '10001:10001' and gate['network_mode'] == 'none'
        assert gate['memory_bytes'] == 4*1024**3 and gate['nano_cpus'] == 2_000_000_000
    identity = release_identity('rag','5',COMMIT); build = native('release/5/build.json')
    receipt = require_distance_receipt(native('release/5/corpus-distance.json'),identity,source_digest,build,bodies['release/5/distance-report.json'])
    recheck = native('release/5/corpus-distance-recheck.json'); assert recheck['passed'] and recheck['report_sha256'] == receipt['report_sha256']
    stages = native('jenkins/5/stages.json')['stages']; assert len(stages) == 8 and all(s['status'] == 'SUCCESS' for s in stages)
    verified = native('release/5/verify.json'); assert verified['actual_successful_tasks'] == 6 and verified['quality_checks'] == 10
    final = native('release/5/airflow-final.json'); tasks = final['tasks']['task_instances']
    assert final['run']['state'] == 'success' and len(tasks) == 6 and all(t['state'] == 'success' for t in tasks)
    assert native('release/5/quality.json')['passed'] and native('release/5/ci-datahub-publication.json')['passed']
    for operation,item in verified['native_containers'].items():
        actual = native('run/native/'+operation+'-container.json')
        assert actual['Id'] == item['id'] and actual['Image'] == item['image'] and actual['State']['ExitCode'] == 0 and not actual['State']['OOMKilled']
    assert native('operator/suite-container.json')['state']['ExitCode'] == 0
    for name in ['pipeline','negative-skipped']:
        assert (base/'ui'/(name+'.jpg')).read_bytes()[:3] == b'\xff\xd8\xff'
    assert 'skipped due to earlier failure(s)' in (base/'ui/negative-skipped.ax.txt').read_text()
    return {'passed':True,'native_payloads':161,'source_files':50,'exact_current_source_files':48,'pinned_docstring_only_files':2,
            'documents_per_version':4,'real_embedding_dimensions':384,
            'corpus_controls':4,'mmd_squared':scores,'maximum_independent_error':max(errors),
            'denied_build':4,'production_build':5,'native_airflow_tasks':6,'quality_checks':10,
            'original_screenshots':2,'hypothesis_test':False,'coursework_complete':False}


if __name__ == '__main__': print(json.dumps(verify(),indent=2))
