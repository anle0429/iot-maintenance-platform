"""Verify actual Airflow RAG execution, persisted quality/governance and a blocked invalid source."""
from hashlib import sha256
import json
from integration_evidence import capsule, read, sources, exited

MANIFEST_SHA256 = '8c6e0ec025b1dbf2b681a103a8652f10909be01e8d4c4bb05b973c81780cddc3'


def verify():
    """Cross-check original task/container states and every publication dependency without rerunning the VM."""
    base, payloads = capsule('rag-orchestration', MANIFEST_SHA256)
    sources(base, 'success', current=True)
    sources(base, 'initial-network-failure')
    sources(base, 'initial-success')
    sources(base, 'invalid-source')
    final = read(base, 'success/native/airflow-final.json')
    tasks = {row['task_id']: row for row in final['tasks']['task_instances']}
    assert final['run']['state'] == 'success' and final['run']['dag_run_id'] == 'rag-airflow-29c'
    expected = {'fetch', 'chunk', 'embed', 'store', 'validate', 'publish_to_datahub'}
    assert set(tasks) == expected and all(row['state'] == 'success' and row['try_number'] == 1 for row in tasks.values())
    release = read(base, 'success/native/release.json')
    for operation in ['fetch', 'chunk', 'embed', 'store', 'validate', 'publish']:
        container = read(base, 'success/native/' + operation + '-container.json')
        exited(container)
        metadata = operation == 'publish'
        assert container['Image'] == release['metadata_image_id' if metadata else 'image_id']
        assert container['Config']['User'] == '10001:10001' and container['HostConfig']['ReadonlyRootfs']
        assert container['HostConfig']['NanoCpus'] == (1 if metadata else 2) * 1_000_000_000
        assert container['HostConfig']['Memory'] == container['HostConfig']['MemorySwap'] == (2 if metadata else 4) * 1024**3
        assert container['HostConfig']['NetworkMode'] == (release['metadata_network_id'] if metadata else 'none')
    quality = read(base, 'success/state/quality.json')
    assert quality == read(base, 'success/native/quality.json') and quality['passed']
    assert len(quality['checks']) == 10 and all(quality['checks'].values())
    for name, digest in quality['artifact_sha256'].items():
        assert sha256((base / 'success/state' / name).read_bytes()).hexdigest() == digest, name
    counts = {name: row['rows'] for name, row in quality['datasets'].items()}
    assert counts == {'documents': 4, 'chunks': 18, 'embeddings': 18, 'offline': 18, 'online': 18}
    assert quality['native_live_entity_count'] == 18
    publication = read(base, 'success/state/datahub-publication.json')
    assert publication == read(base, 'success/native/datahub-publication.json') and publication['passed']
    assert publication['quality_sha256'] == sha256((base / 'success/state/quality.json').read_bytes()).hexdigest()
    assert publication['acknowledged_aspects'] == 52 and len(publication['datasets']) == 5
    assert set(publication['assertions']) == {'urn:li:assertion:iot-rag-' + sha256(name.encode()).hexdigest() for name in quality['checks']}
    actual = {row['urn']: row for row in publication['datasets']}
    for name, descriptor in quality['datasets'].items():
        platform = 'feast' if name == 'online' else 'parquet' if name == 'offline' else 'file'
        urn = f'urn:li:dataset:(urn:li:dataPlatform:{platform},iot.knowledge.{name},DEV)'
        assert actual[urn]['fields'] == [field['name'] for field in descriptor['fields']]
        assert {row.split('iot.knowledge.')[1].split(',')[0] for row in actual[urn]['upstreams']} == set(descriptor['upstream'])
    failed = read(base, 'invalid-source/native/airflow-final.json')
    states = {row['task_id']: row for row in failed['tasks']['task_instances']}
    assert failed['run']['state'] == 'failed' and states['fetch']['state'] == 'failed'
    assert all(states[name]['state'] == 'upstream_failed' and states[name]['try_number'] == 0 for name in expected - {'fetch'})
    exited(read(base, 'invalid-source/native/fetch-container.json'), 1)
    assert 'Source size/hash mismatch: monitoring' in (base / 'invalid-source/native/fetch.log').read_text()
    catalog = read(base, 'invalid-source/source/tree/knowledge/catalog.json')
    source_hash = next(row['sha256'] for row in catalog['documents'] if row['document_id'] == 'monitoring')
    assert sha256((base / 'invalid-source/source/tree/knowledge/monitoring.md').read_bytes()).hexdigest() != source_hash
    initial = read(base, 'initial-network-failure/native/airflow-final.json')
    assert initial['run']['state'] == 'failed'
    assert read(base, 'restored-owned-link.json')['all_runs_terminal']
    for name in ['airflow-rag-c-success.jpg', 'airflow-rag-invalid-source.jpg', 'datahub-rag-search.jpg', 'datahub-rag-lineage.jpg', 'datahub-rag-c-quality.jpg', 'datahub-rag-c-identity-history.jpg']:
        assert (base / 'ui' / name).read_bytes().startswith(b'\xff\xd8\xff')
    return {'passed': True, 'payloads': payloads, 'actual_successful_tasks': 6, 'persisted_checks': 10,
            'native_datasets': 5, 'source_rejection_blocks_downstream': True, 'original_ui_captures': 8,
            'scope': 'Recorded native Airflow/Feast/DataHub run and indexed UI. CI, schema-evolution rollout, MCP/agents and current readiness remain separate.'}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
