"""Publish actual RAG stage schemas, lineage, jobs and source-bound validation assertions to DataHub."""
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import argparse
import json
import os


def publish(directory, run_id):
    """Require validated immutable artifacts before acknowledged metadata writes and independent GMS readback."""
    from datahub.emitter.mce_builder import make_data_flow_urn, make_data_job_urn, make_dataset_urn
    from datahub.emitter.mcp import MetadataChangeProposalWrapper
    from datahub.emitter.rest_emitter import DatahubRestEmitter
    from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
    import datahub.metadata.schema_classes as S
    directory = Path(directory); quality = json.loads((directory / 'quality.json').read_bytes())
    if not quality['passed'] or not all(quality['checks'].values()): raise ValueError('Refuse unvalidated knowledge metadata')
    for name, digest in quality['artifact_sha256'].items():
        if sha256((directory / name).read_bytes()).hexdigest() != digest: raise ValueError('Validated artifacts changed before publication')
    endpoint = os.environ['DATAHUB_GMS_URL']; emitter = DatahubRestEmitter(gms_server=endpoint)
    graph = DataHubGraph(DatahubClientConfig(server=endpoint)); emitter.test_connection()
    dag = 'iot_rag_knowledge'; flow = make_data_flow_urn('airflow', dag, 'iot-vm')
    url = 'http://localhost:18480/dags/' + dag + '/runs/' + run_id
    urns = {name: make_dataset_urn('feast' if name == 'online' else 'parquet' if name == 'offline' else 'file', 'iot.knowledge.'+name, 'DEV') for name in quality['datasets']}
    emitted = []

    def emit(urn, aspect):
        """Acknowledge each original aspect without treating acknowledgement as indexed UI proof."""
        emitter.emit_mcp(MetadataChangeProposalWrapper(entityUrn=urn, aspect=aspect)); emitted.append({'urn': urn, 'aspect': aspect.ASPECT_NAME})

    try:
        emit('urn:li:dataPlatform:feast', S.DataPlatformInfoClass(name='feast', displayName='Feast knowledge online', type='KEY_VALUE_STORE', datasetNameDelimiter='.'))
        emit(flow, S.DataFlowInfoClass(name=dag, description='Versioned authored documents to native embeddings and isolated Feast/Milvus, validated before publication.', externalUrl=url,
                                      customProperties={'run': run_id, 'embedding_revision': quality['embedding_config']['revision']}))
        timestamp = int(datetime.fromisoformat(quality['checked_utc']).timestamp() * 1000)
        for name, descriptor in quality['datasets'].items():
            urn = urns[name]; field_rows = descriptor['fields']
            typed = []
            for field in field_rows:
                native = field['type']; kind = S.ArrayTypeClass if 'list' in native or native == 'array' else S.NumberTypeClass if native in ['integer','number','int64','double','float'] else S.StringTypeClass
                typed.append(S.SchemaFieldClass(fieldPath=field['name'], nativeDataType=native, nullable=field['nullable'], type=S.SchemaFieldDataTypeClass(type=kind())))
            emit(urn, S.DatasetPropertiesClass(name='knowledge/'+name, description='Actual RAG '+name+' stage; synthetic authored references.',
                                               customProperties={'run': run_id, 'embedding_revision': quality['embedding_config']['revision'], 'rows': str(descriptor['rows'])}))
            emit(urn, S.SchemaMetadataClass(schemaName='knowledge/'+name, platform='urn:li:dataPlatform:'+('feast' if name=='online' else 'parquet' if name=='offline' else 'file'),
                                           version=0, hash=sha256(json.dumps(field_rows, sort_keys=True).encode()).hexdigest(), platformSchema=S.OtherSchemaClass(rawSchema=json.dumps(field_rows)), fields=typed))
            emit(urn, S.DatasetProfileClass(timestampMillis=timestamp, rowCount=descriptor['rows'], columnCount=len(typed)))
            emit(urn, S.UpstreamLineageClass(upstreams=[S.UpstreamClass(dataset=urns[parent], type='TRANSFORMED') for parent in descriptor['upstream']]))
        stages = {'fetch': ([], ['documents']), 'chunk': (['documents'], ['chunks']), 'embed': (['chunks'], ['embeddings']),
                  'store': (['chunks','embeddings'], ['offline','online']), 'validate': (['online','offline'], [])}
        for stage, (inputs, outputs) in stages.items():
            job = make_data_job_urn('airflow', dag, stage, 'iot-vm')
            emit(job, S.DataJobInfoClass(name=stage, type='COMMAND', flowUrn=flow, externalUrl=url, customProperties={'run': run_id}))
            emit(job, S.DataJobInputOutputClass(inputDatasets=[urns[n] for n in inputs], outputDatasets=[urns[n] for n in outputs]))
        assertions = []
        for name, passed in quality['checks'].items():
            urn = 'urn:li:assertion:iot-rag-' + sha256(name.encode()).hexdigest()
            emit(urn, S.AssertionInfoClass(type='CUSTOM', description=name, source=S.AssertionSourceClass(type='EXTERNAL'),
                                          customAssertion=S.CustomAssertionInfoClass(type='Native persisted RAG contract', entity=urns['online'], logic=name+' == true')))
            emit(urn, S.AssertionRunEventClass(timestampMillis=timestamp, runId=run_id, asserteeUrn=urns['online'], assertionUrn=urn, status='COMPLETE',
                                              result=S.AssertionResultClass(type='SUCCESS' if passed else 'FAILURE', nativeResults={'observed': str(passed)}, externalUrl=url)))
            assertions.append(urn)
        readback = []
        for name, urn in urns.items():
            schema = graph.get_aspect(urn, S.SchemaMetadataClass); lineage = graph.get_aspect(urn, S.UpstreamLineageClass)
            if not schema or [field.fieldPath for field in schema.fields] != [field['name'] for field in quality['datasets'][name]['fields']]: raise ValueError('Actual GMS schema differs')
            if lineage is None or {u.dataset for u in lineage.upstreams} != {urns[n] for n in quality['datasets'][name]['upstream']}: raise ValueError('Actual GMS lineage differs')
            readback.append({'urn': urn, 'fields': [field.fieldPath for field in schema.fields], 'upstreams': [u.dataset for u in lineage.upstreams]})
        result = {'passed': True, 'run': run_id, 'flow': flow, 'datasets': readback, 'assertions': assertions, 'acknowledged_aspects': len(emitted),
                  'quality_sha256': sha256((directory / 'quality.json').read_bytes()).hexdigest(),
                  'scope': 'Native GMS schema/lineage readback and acknowledged assertion events; indexed UI/history verified separately.'}
        with (directory / 'datahub-publication.json').open('x') as stream: json.dump(result, stream, indent=2)
        return result
    finally:
        emitter.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--state', type=Path, required=True); parser.add_argument('--run', required=True)
    args = parser.parse_args(); print(json.dumps(publish(args.state, args.run), indent=2))
