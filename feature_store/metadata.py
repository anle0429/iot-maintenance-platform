"""Publish the real compact-to-online Feast lineage and validated materialization assertions to DataHub."""

import argparse
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path

from datahub.emitter.mce_builder import make_data_flow_urn, make_data_job_urn, make_dataset_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
import datahub.metadata.schema_classes as S

from feature_store.common import digest, save, utc_now


def publish(directory: Path):
    """Read bound validation results, emit synchronous aspects and verify GMS schema/lineage/job readback."""
    validation = json.loads((directory / 'validation.json').read_text())
    materialization = json.loads((directory / 'materialization.json').read_text())
    if (not validation['passed'] or not materialization['passed']
            or validation['materialization_report_sha256'] != digest(directory / 'materialization.json')
            or json.loads((directory / '_MATERIALIZED_SUCCESS.json').read_text())['report_sha256'] != digest(directory / 'materialization.json')):
        raise ValueError('Refuse publication of an unverified materialization')
    binding = validation['binding']
    run = directory.name
    endpoint = os.environ['DATAHUB_GMS_URL']
    emitter = DatahubRestEmitter(gms_server=endpoint)
    graph = DataHubGraph(DatahubClientConfig(server=endpoint))
    emitter.test_connection()
    dag = 'iot_feast_materialization'
    flow = make_data_flow_urn('airflow', dag, 'iot-vm')
    job = make_data_job_urn('airflow', dag, 'materialize_incremental', 'iot-vm')
    original = make_dataset_urn('parquet', 'iot.medium.features.feat_machine_10m', 'DEV')
    compact = make_dataset_urn('parquet', 'iot.feast.machine_rolling_batch', 'DEV')
    online = make_dataset_urn('redis', 'iot.feast.iot_materialization.machine_rolling_10m', 'DEV')
    url = 'http://localhost:18480/dags/' + dag + '/runs/' + run
    properties = {'run': run, 'protocol_sha256': binding['protocol_sha256'], 'compact_sha256': binding['compact_sha256'],
                  'cutoff_simulated_utc': validation['cutoff'], 'mode': materialization['mode'],
                  'validation_sha256': digest(directory / 'validation.json')}
    def emit(urn, aspect):
        """Do not treat an acknowledgement as indexed assertion history proof."""
        emitter.emit_mcp(MetadataChangeProposalWrapper(entityUrn=urn, aspect=aspect))
    try:
        emit('urn:li:dataPlatform:redis', S.DataPlatformInfoClass(name='redis', displayName='Redis (Feast online)', type='KEY_VALUE_STORE', datasetNameDelimiter='.'))
        emit(flow, S.DataFlowInfoClass(name=dag, description='Verified immutable offline features to incremental online vectors on the simulated clock.', externalUrl=url, customProperties=properties))
        emit(job, S.DataJobInfoClass(name='materialize_incremental', type='COMMAND', flowUrn=flow, externalUrl=url, customProperties=properties))
        emit(job, S.DataJobInputOutputClass(inputDatasets=[compact], outputDatasets=[online]))
        emit(original, S.DatasetPropertiesClass(name='medium/feat_machine_10m', description='Original128-file arrival-aware60-day feature snapshot. Separate from the48-hour DP3 pilot.',
             customProperties={'path': 'data/feature-datasets/feature-medium-27-v2/artifacts/features/feat_machine_10m', 'rows': '3456000', 'protocol_sha256': binding['protocol_sha256']}))
        emit(compact, S.DatasetPropertiesClass(name='feast/machine_rolling_batch', description='Immutable losslessly compacted offline Feast source.', customProperties={**properties, 'path': binding['source'], 'rows': '3456000'}))
        emit(compact, S.UpstreamLineageClass(upstreams=[S.UpstreamClass(dataset=original, type='TRANSFORMED')]))
        emit(online, S.DatasetPropertiesClass(name='feast/online/machine_rolling_10m', description='Logical native Feast online response: machine entity and eight model inputs, all200 machines validated against the offline cutoff.', customProperties=properties))
        fields = [S.SchemaFieldClass(fieldPath='machine_id', nativeDataType='string', nullable=False, type=S.SchemaFieldDataTypeClass(type=S.StringTypeClass()))]
        fields += [S.SchemaFieldClass(fieldPath=n, nativeDataType='int64' if n in ('reading_count_10m', 'prior_failures_24h') else 'float64', nullable=False,
                   type=S.SchemaFieldDataTypeClass(type=S.NumberTypeClass())) for n in binding['features']]
        emit(online, S.SchemaMetadataClass(schemaName='machine_rolling_10m', platform='urn:li:dataPlatform:redis', version=0,
             hash=sha256(json.dumps(binding['features']).encode()).hexdigest(), platformSchema=S.OtherSchemaClass(rawSchema=json.dumps(binding['features'])), fields=fields))
        emit(online, S.UpstreamLineageClass(upstreams=[S.UpstreamClass(dataset=compact, type='TRANSFORMED')]))
        timestamp = int(datetime.fromisoformat(validation['validated_utc']).timestamp()*1000)
        emit(online, S.DatasetProfileClass(timestampMillis=timestamp, rowCount=validation['online_comparison']['machines'], columnCount=len(fields)))
        assertions = []
        for name, passed in validation['checks'].items():
            assertion = 'urn:li:assertion:iot-feast-' + sha256(name.encode()).hexdigest()
            emit(assertion, S.AssertionInfoClass(type='CUSTOM', description=name, source=S.AssertionSourceClass(type='EXTERNAL'),
                 customAssertion=S.CustomAssertionInfoClass(type='Feast materialization contract', entity=online, logic=name+' == true')))
            emit(assertion, S.AssertionRunEventClass(timestampMillis=timestamp, runId=run, asserteeUrn=online, assertionUrn=assertion,
                 status='COMPLETE', result=S.AssertionResultClass(type='SUCCESS' if passed else 'FAILURE', externalUrl=url)))
            assertions.append({'urn': assertion, 'name': name, 'passed': passed})
        schema = graph.get_aspect(online, S.SchemaMetadataClass)
        lineage = graph.get_aspect(online, S.UpstreamLineageClass)
        io = graph.get_aspect(job, S.DataJobInputOutputClass)
        if (schema is None or [x.fieldPath for x in schema.fields] != ['machine_id', *binding['features']]
                or lineage is None or [x.dataset for x in lineage.upstreams] != [compact]
                or io is None or io.inputDatasets != [compact] or io.outputDatasets != [online]):
            raise ValueError('DataHub online schema, table lineage or producing job readback differs')
        for item in assertions:
            definition = graph.get_aspect(item['urn'], S.AssertionInfoClass)
            if definition is None or definition.customAssertion.entity != online:
                raise ValueError('DataHub materialization assertion definition differs')
        proof = {'published': True, 'run': run, 'flow': flow, 'job': job, 'input': compact, 'output': online,
                 'original_feature_input': original, 'schema_fields': [x.fieldPath for x in schema.fields],
                 'assertions': assertions, 'validation_sha256': digest(directory/'validation.json'),
                 'code_sha256': digest(Path(__file__)), 'published_utc': utc_now(), 'sdk': '1.6.0',
                 'scope': 'Actual GMS schema/lineage/job/assertion definitions; indexed lineage and run history require separate GraphQL readback.'}
        save(directory / 'datahub-publication.json', proof)
        print({'published': True, 'assertions': len(assertions)}, flush=True)
    finally:
        emitter.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports', type=Path, required=True)
    publish(parser.parse_args().reports)
