"""Verify original Jenkins RAG stages, exact source/image identities and persisted Airflow/Feast/DataHub facts offline."""
from datetime import datetime,timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import math
import sys
import tarfile
import xml.etree.ElementTree as ET
from integration_evidence import ROOT,capsule,read,exited
sys.path.insert(0,str(ROOT))
from agents.ci_contract import release_identity,require_receipt,validate_source
from rag.corpus import chunk_documents

MANIFEST_SHA256='ffd4a036defcb58d0388ab208d45aa126a674c01d1baa0ed1eaa91ba26cef62a'
COMMIT='9b77c06fe872d588e4f7e55af8c5834f2b03448e'
TASKS=['fetch','chunk','embed','store','validate','publish_to_datahub']


def verify():
    """Reconcile native processes and raw artifacts rather than accepting a successful receipt by itself."""
    base,count=capsule('ci-rag',MANIFEST_SHA256);provenance=read(base,'native-archive.json')
    raw=(base/'native-receipts.tar.gz').read_bytes()
    assert len(raw)==provenance['archive_bytes']==1063921
    assert sha256(raw).hexdigest()==provenance['archive_sha256']=='4e52a97bf09fd19f7578245227d1cb14b47c7aa3d1a349a920167e8c913b212d'
    bodies={}
    with tarfile.open(fileobj=BytesIO(raw),mode='r:gz') as package:
        for member in package:
            assert member.isfile() or member.isdir()
            if not member.isfile():continue
            name=Path(member.name).relative_to('rag-ci-capture-29a').as_posix()
            assert '..' not in Path(name).parts and name not in bodies
            bodies[name]=package.extractfile(member).read()
    inventory=json.loads(bodies['manifest.json'])
    assert sha256(bodies['manifest.json']).hexdigest()==provenance['original_inventory_sha256']
    assert len(inventory['files'])==provenance['native_payloads']==260
    assert set(bodies)==set(inventory['files'])|{'manifest.json'}
    for name,metadata in inventory['files'].items():
        assert len(bodies[name])==metadata['bytes'] and sha256(bodies[name]).hexdigest()==metadata['sha256'],name
    def original(name):
        """Read one inventory-authenticated original JSON response."""
        return json.loads(bodies[name])
    for number,result in [(1,'FAILURE'),(2,'SUCCESS'),(3,'SUCCESS')]:
        prefix=str(number);job=original('jenkins/'+prefix+'/build.json');seal=original('source/'+prefix+'/manifest.json')
        assert job['number']==number and not job['building'] and job['result']==result
        parameters={p['name']:p['value'] for a in job['actions'] for p in a.get('parameters',[])}
        assert parameters=={'SOURCE_COMMIT':seal['commit']}
        for name,digest in seal['files'].items():assert sha256(bodies['source/'+prefix+'/tree/'+name]).hexdigest()==digest
        console=bodies['jenkins/'+prefix+'/console.txt'].decode()
        assert console.rstrip().endswith('Finished: '+result)
    assert b'pull access denied' in bodies['release/1/application-build.txt']
    assert 'release/1/build.json' not in bodies and 'release/1/deploy.json' not in bodies
    seal=original('source/3/manifest.json');assert seal['commit']==COMMIT and len(seal['files'])==41
    source_digest=validate_source(ROOT,seal,COMMIT)
    for name in seal['files']:assert bodies['source/3/tree/'+name]==(ROOT/name).read_bytes(),name
    definition=ET.fromstring(bodies['jenkins/job-config.xml'])
    assert definition.find('definition/script').text.encode()==bodies['source/3/tree/infra/ci/Rag.Jenkinsfile']
    stages=original('jenkins/3/stages.json')
    assert stages['status']=='SUCCESS' and len(stages['stages'])==7 and all(s['status']=='SUCCESS' for s in stages['stages'])
    assert (base/'jenkins-stages.json').read_bytes()==bodies['jenkins/3/stages.json']
    identity=release_identity('rag','3',COMMIT);phases={}
    for phase in ['test','build','deploy','verify']:
        phases[phase]=require_receipt(original('release/3/'+phase+'.json'),identity,phase)
    assert phases['test']['source_digest']==source_digest
    assert phases['test']['test_console_sha256']==sha256(bodies['release/3/contracts.txt']).hexdigest()
    assert b'Ran 13 tests' in bodies['release/3/contracts.txt'] and bodies['release/3/contracts.txt'].rstrip().endswith(b'OK')
    controller=original('release/3/controller.json')['id']
    for phase in ['test','deploy','verify']:
        native=original('release/3/'+phase+'-container.json')
        assert native['image']==controller and native['commit']==COMMIT
        assert native['state']['ExitCode']==0 and not native['state']['Running'] and not native['state']['OOMKilled']
        assert native['memory_bytes']==2*1024**3 and native['nano_cpus']==1_000_000_000
    build=phases['build'];deploy=phases['deploy'];accepted=phases['verify'];run='rag-ci-9b77c06f-3'
    assert build['revision_label']==COMMIT and build['dependency_image_id']=='sha256:4d65bdfb955f2d459f750fba5f30f381aab0378a4ce95f9047359a5760cd1afd'
    expected={name:digest for name,digest in seal['files'].items() if name.startswith(('rag/','knowledge/')) or name=='infra/rag/embedding.lock.json'}
    assert build['packaged_sha256']==expected
    release=original('run/3/manifest.json');assert release==deploy['manifest']
    assert release['source_files']==seal['files'] and release['ci_source_commit']==COMMIT and release['ci_release']==identity['release']
    assert release['image_id']==build['image_id'] and deploy['dag_sha256']==seal['files']['infra/ci/iot_ci_rag.py']
    assert deploy['dag_id']==accepted['dag_id']=='iot_ci_rag_knowledge' and deploy['run_id']==accepted['run_id']==run
    final=original('release/3/airflow-final.json');tasks={t['task_id']:t for t in final['tasks']['task_instances']}
    assert final['run']['state']=='success' and final['run']['dag_run_id']==run and set(tasks)==set(TASKS)
    assert all(t['state']=='success' and t['try_number']==1 for t in tasks.values())
    for operation in ['fetch','chunk','embed','store','validate','publish']:
        native=original('run/3/native/'+operation+'-container.json');exited(native)
        metadata=operation=='publish';expected_image=release['metadata_image_id'] if metadata else build['image_id']
        assert native['Image']==expected_image and accepted['native_containers'][operation]=={'id':native['Id'],'image':expected_image,'exit_code':0}
        assert native['Config']['User']=='10001:10001' and native['HostConfig']['ReadonlyRootfs']
        assert native['HostConfig']['Memory']==native['HostConfig']['MemorySwap']==(2 if metadata else 4)*1024**3
        assert native['HostConfig']['NanoCpus']==(1 if metadata else 2)*1_000_000_000
        assert native['HostConfig']['NetworkMode']==(release['metadata_network_id'] if metadata else 'none')
        mounts={m['Destination']:m for m in native['Mounts']}
        assert not mounts['/workspace']['RW'] and mounts['/workspace']['Source'].endswith(release['source_directory'])
    state='run/3/state/';quality=original(state+'quality.json')
    assert quality['passed'] and len(quality['checks'])==10 and all(quality['checks'].values())
    assert {name:value['rows'] for name,value in quality['datasets'].items()}=={'documents':4,'chunks':18,'embeddings':18,'offline':18,'online':18}
    for name,digest in quality['artifact_sha256'].items():assert sha256(bodies[state+name]).hexdigest()==digest
    documents=original(state+'documents.json')['documents'];chunks=original(state+'chunks.json')
    assert chunks==chunk_documents(documents) and len({row['chunk_id'] for row in chunks})==18
    for doc in documents:assert doc['text'].encode()==bodies['source/3/tree/knowledge/'+doc['file']] and sha256(doc['text'].encode()).hexdigest()==doc['sha256']
    vectors=original(state+'vectors.json');assert len(vectors['vectors'])==18 and vectors['embedding_config']==quality['embedding_config']
    for vector in vectors['vectors']:assert len(vector)==384 and all(math.isfinite(x) for x in vector) and abs(sum(x*x for x in vector)-1)<.001
    replay=original(state+'store-readback.json');assert replay['first']==replay['replay']
    assert replay['parquet_sha256']==sha256(bodies[state+'chunks.parquet']).hexdigest()
    for index,chunk in enumerate(chunks):
        for name in ['text','document_id','revision','source_sha256','section']:assert quality['online_readback'][name][index]==chunk[name]
    questions=original(state+'retrieval.json');assert len(questions)==3
    for query in questions:assert query['expected_document'] in query['result']['document_id'][:3]
    governance=original(state+'ci-datahub-publication.json');canonical=original(state+'datahub-publication.json')
    assert governance['passed'] and canonical['passed'] and canonical['run']==run
    assert governance['source_commit']==COMMIT and governance['run_id']==run and governance['ci_release']==identity['release']
    assert governance['quality_sha256']==canonical['quality_sha256']==sha256(bodies[state+'quality.json']).hexdigest()
    assert governance['canonical_publication_sha256']==sha256(bodies[state+'datahub-publication.json']).hexdigest()
    assert len(governance['emitted'])==13 and set(governance['readback'])=={'flow',*TASKS}
    assert len(canonical['datasets'])==5 and len(canonical['assertions'])==10
    expected_url='http://localhost:18480/dags/iot_ci_rag_knowledge/runs/'+run
    properties={'run_id':run,'source_commit':COMMIT,'ci_release':identity['release']}
    assert governance['external_url']==expected_url and governance['readback']['flow']['externalUrl']==expected_url
    assert governance['readback']['flow']['customProperties']==properties
    for task in TASKS:
        info=governance['readback'][task]['info']
        assert info['name']==task and info['flowUrn']==governance['flow_urn'] and info['customProperties']==properties and info['externalUrl']==expected_url
    for public,native in [('verification.json','release/3/verify.json'),('quality.json',state+'quality.json'),('ci-datahub-publication.json',state+'ci-datahub-publication.json')]:
        assert (base/public).read_bytes()==bodies[native]
    assert accepted['actual_successful_tasks']==6 and accepted['quality_checks']==10 and accepted['coursework_complete'] is False
    ui=read(base/'ui','index.json');assert ui['original_screenshots'] and len(ui['entries'])==2
    for entry in ui['entries']:
        assert entry['format']=='JPEG' and entry['pixels_edited'] is False
        assert (base/'ui'/entry['image']).read_bytes().startswith(b'\xff\xd8\xff')
        ax=(base/'ui'/entry['accessibility']).read_text(encoding='utf-8');assert entry['url'] in ax
    return {'passed':True,'public_payloads':count,'native_payloads':260,'exact_current_source_files':41,
            'jenkins_build':3,'all_seven_stages_successful':True,'native_airflow_tasks':6,'first_attempt_tasks':6,
            'quality_checks':10,'source_documents':4,'native_chunks':18,'datahub_ci_jobs':6,
            'failed_builds_retained':1,'original_ui_captures':2,'coursework_complete':False}


if __name__=='__main__':print(json.dumps(verify(),indent=2))
