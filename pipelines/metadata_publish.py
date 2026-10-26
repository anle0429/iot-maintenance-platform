"""Publish actual DP schemas, producing jobs, table lineage and external assertion results to DataHub."""

import argparse
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import time

from datahub.emitter.mce_builder import make_data_flow_urn, make_data_job_urn, make_dataset_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
import datahub.metadata.schema_classes as S
from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig

from pipelines.orchestration_contract import STAGES, TABLES, digest, save, stage_path, utc_now, verified_manifest


DAGS = {'dp1': 'iot_dp1_bronze', 'dp2': 'iot_dp2_warehouse', 'dp3': 'iot_dp3_features'}
DESCRIPTIONS = {'dp1': 'Raw JSONL to typed Bronze, preserving duplicate exports and provenance.',
                'dp2': 'Validated Bronze to unique Silver readings and Gold machine dimensions/facts.',
                'dp3': 'Availability-aware 10-minute features and separate censored 1-hour labels.'}


def bootstrap_viewer(endpoint: str) -> None:
    """Provision the private VM's learner UI identity before the LTS server can issue a login session."""
    emitter = DatahubRestEmitter(gms_server=endpoint)
    emitter.emit_mcp(MetadataChangeProposalWrapper(entityUrn='urn:li:dataPlatform:parquet',
                     aspect=S.DataPlatformInfoClass(name='parquet', displayName='Parquet (VM files)',
                                                    type='FILE_SYSTEM', datasetNameDelimiter='.')))
    urn = 'urn:li:corpuser:learner'
    for aspect in (S.CorpUserInfoClass(active=True, displayName='Coursework learner'), S.StatusClass(removed=False),
                   S.RoleMembershipClass(roles=['urn:li:dataHubRole:Admin'])):
        emitter.emit_mcp(MetadataChangeProposalWrapper(entityUrn=urn, aspect=aspect))
    emitter.close()
    print('Private learner identity provisioned; credentials remain in the local secret file.')


def dataset_id(name: str) -> str:
    """Use stable logical dataset URNs; immutable run paths and assertion history carry the run version."""
    return make_dataset_urn('file' if name.startswith('raw/') else 'parquet', f'iot.{name.replace("/", ".")}', 'DEV')


def dependencies(stage: str, table: str) -> list[str]:
    """Describe real table dependencies, including intermediate Silver/Gold outputs within DP2."""
    if stage == 'dp1':
        return ['raw/' + table.split('raw_')[1]]
    mapping = {
        'silver/stg_telemetry': ['bronze/raw_telemetry'], 'silver/stg_failures': ['bronze/raw_failures'],
        'silver/stg_maintenance': ['bronze/raw_maintenance'], 'gold/dim_machine': ['bronze/raw_machines'],
        'gold/dim_factory': ['gold/dim_machine'], 'gold/fact_telemetry': ['silver/stg_telemetry', 'gold/dim_machine'],
        'gold/fact_failure': ['silver/stg_failures', 'gold/dim_machine'],
        'gold/fact_maintenance': ['silver/stg_maintenance', 'gold/dim_machine'],
        'features/feat_machine_10m': ['silver/stg_telemetry', 'silver/stg_failures', 'gold/dim_machine'],
        'features/label_failure_1h': ['silver/stg_telemetry', 'silver/stg_failures', 'silver/stg_maintenance'],
    }
    return mapping[table]


def schema_aspect(name: str, schema: dict):
    """Map the actual Spark readback schema to DataHub field types, retaining native types."""
    types = {'string': S.StringTypeClass, 'timestamp': S.TimeTypeClass, 'integer': S.NumberTypeClass,
             'long': S.NumberTypeClass, 'double': S.NumberTypeClass, 'boolean': S.BooleanTypeClass}
    fields = [S.SchemaFieldClass(fieldPath=f['name'], nativeDataType=str(f['type']), nullable=f['nullable'],
                                type=S.SchemaFieldDataTypeClass(type=types.get(f['type'], S.StringTypeClass)()))
              for f in schema['fields']]
    return S.SchemaMetadataClass(schemaName=name, platform='urn:li:dataPlatform:parquet', version=0,
                                 hash=sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest(),
                                 platformSchema=S.OtherSchemaClass(rawSchema=json.dumps(schema)), fields=fields)


def publish(root: Path, batch: str, stage: str, endpoint: str) -> dict:
    """Emit metadata only from bound persisted reports, then read every schema/job/aspect back from GMS."""
    directory = stage_path(root, batch, stage)
    manifest = verified_manifest(directory, require_validated=False)
    validation = json.loads((directory / 'validation.json').read_text())
    if validation['manifest_sha256'] != digest(directory / '_INGESTED.json'):
        raise ValueError('validation report is bound to different bytes')
    emitter = DatahubRestEmitter(gms_server=endpoint)
    graph = DataHubGraph(DatahubClientConfig(server=endpoint))
    emitter.test_connection()
    dag = DAGS[stage]
    flow = make_data_flow_urn('airflow', dag, 'iot-vm')
    job = make_data_job_urn('airflow', dag, 'ingest', 'iot-vm')
    url = f'http://localhost:18480/dags/{dag}/runs/{batch}'
    emitted = []

    def emit(urn: str, aspect) -> None:
        """Use synchronous acknowledged writes; failed metadata transport fails the Airflow publish task."""
        emitter.emit_mcp(MetadataChangeProposalWrapper(entityUrn=urn, aspect=aspect))
        emitted.append({'urn': urn, 'aspect': aspect.ASPECT_NAME})

    emit(flow, S.DataFlowInfoClass(name=dag, description=DESCRIPTIONS[stage], externalUrl=url,
                                  customProperties={'batch': batch, 'orchestrator': 'Airflow 3.1.7', 'validation': str(validation['passed'])}))
    outputs = [dataset_id(table) for table in TABLES[stage]]
    inputs = sorted({dataset_id(name) for table in TABLES[stage] for name in dependencies(stage, table)} - set(outputs))
    emit(job, S.DataJobInfoClass(name='ingest', type='COMMAND', flowUrn=flow, externalUrl=url,
                               description=DESCRIPTIONS[stage], customProperties={'batch': batch, 'source_manifest_sha256': manifest['source_manifest_sha256']}))
    emit(job, S.DataJobInputOutputClass(inputDatasets=inputs, outputDatasets=outputs))
    validation_job = make_data_job_urn('airflow', dag, 'validate', 'iot-vm')
    emit(validation_job, S.DataJobInfoClass(name='validate', type='COMMAND', flowUrn=flow, externalUrl=url,
                                          description='Validate actual persisted Parquet before downstream DAGs run.',
                                          customProperties={'batch': batch, 'passed': str(validation['passed']), 'checks': str(len(validation['checks']))}))
    emit(validation_job, S.DataJobInputOutputClass(inputDatasets=outputs, outputDatasets=[]))
    timestamp = int(datetime.fromisoformat(validation['validated_utc']).timestamp() * 1000)
    assertions = []
    for table, metadata in manifest['tables'].items():
        urn = dataset_id(table)
        emit(urn, S.DatasetPropertiesClass(name=table, description=f'{DESCRIPTIONS[stage]} Produced by {dag}.',
                                          customProperties={'batch': batch, 'path': f'data/orchestrated/{batch}/{stage}/{table}',
                                                            'rows': str(metadata['rows']), 'source_manifest_sha256': manifest['source_manifest_sha256']}))
        emit(urn, schema_aspect(table, metadata['schema']))
        emit(urn, S.DatasetProfileClass(timestampMillis=timestamp, rowCount=metadata['rows'], columnCount=len(metadata['schema']['fields'])))
        upstreams = dependencies(stage, table)
        if stage == 'dp1':
            for name in upstreams:
                source_urn = dataset_id(name)
                emit(source_urn, S.DatasetPropertiesClass(name=name, description='Immutable catalog-verified JSONL source (logical source entity).',
                                                         customProperties={'format': 'JSONL', 'path': 'data/source/orchestration-pilot',
                                                                           'source_manifest_sha256': manifest['source_manifest_sha256']}))
        emit(urn, S.UpstreamLineageClass(upstreams=[S.UpstreamClass(dataset=dataset_id(name), type='TRANSFORMED') for name in upstreams]))
    for check in validation['checks']:
        urn = dataset_id(check['table'])
        key = sha256(f"{stage}/{check['table']}/{check['name']}".encode()).hexdigest()
        assertion = f'urn:li:assertion:iot-{key}'
        emit(assertion, S.AssertionInfoClass(type='CUSTOM', description=check['name'], source=S.AssertionSourceClass(type='EXTERNAL'),
                                             customAssertion=S.CustomAssertionInfoClass(type='Spark persisted-data contract', entity=urn,
                                                                                        logic=f"{check['name']} == {json.dumps(check['expected'], sort_keys=True)}")))
        emit(assertion, S.DataPlatformInstanceClass(platform='urn:li:dataPlatform:airflow', instance='urn:li:dataPlatformInstance:(urn:li:dataPlatform:airflow,iot-vm)'))
        result = 'SUCCESS' if check['passed'] else 'FAILURE'
        emit(assertion, S.AssertionRunEventClass(timestampMillis=timestamp, runId=batch, asserteeUrn=urn, assertionUrn=assertion,
                                                 status='COMPLETE', result=S.AssertionResultClass(type=result,
                                                 nativeResults={'observed': json.dumps(check['observed'], sort_keys=True), 'expected': json.dumps(check['expected'], sort_keys=True)}, externalUrl=url)))
        assertions.append({'urn': assertion, 'dataset': urn, 'name': check['name'], 'result': result})
    # GMS aspect storage is synchronous; search/lineage indexes are eventually consistent.
    readback = []
    for table in TABLES[stage]:
        aspect = graph.get_aspect(dataset_id(table), S.SchemaMetadataClass)
        expected = manifest['tables'][table]['schema']['fields']
        if aspect is None or [f.fieldPath for f in aspect.fields] != [f['name'] for f in expected]:
            raise ValueError('DataHub schema readback mismatch')
        lineage = graph.get_aspect(dataset_id(table), S.UpstreamLineageClass)
        if lineage is None or {u.dataset for u in lineage.upstreams} != {dataset_id(n) for n in dependencies(stage, table)}:
            raise ValueError('DataHub table lineage readback mismatch')
        readback.append({'dataset': dataset_id(table), 'schema_fields': len(aspect.fields), 'upstreams': [u.dataset for u in lineage.upstreams]})
    job_aspect = graph.get_aspect(job, S.DataJobInputOutputClass)
    if job_aspect is None or set(job_aspect.outputDatasets) != set(outputs) or set(job_aspect.inputDatasets) != set(inputs):
        raise ValueError('DataHub producing job readback mismatch')
    for item in assertions:
        aspect = graph.get_aspect(item['urn'], S.AssertionInfoClass)
        if aspect is None or aspect.customAssertion.entity != item['dataset']:
            raise ValueError('DataHub assertion definition readback mismatch')
    result = {'published': True, 'batch': batch, 'stage': stage, 'validation_passed': validation['passed'],
              'published_utc': utc_now(), 'flow': flow, 'job': job, 'job_inputs': inputs, 'job_outputs': outputs,
              'datasets_read_back': readback, 'assertions': assertions, 'acknowledged_aspects': len(emitted),
              'validation_sha256': digest(directory / 'validation.json'), 'sdk': '1.6.0',
              'scope': 'GMS schema/lineage/job/assertion definition readback; assertion run history verified separately via GraphQL.'}
    save(directory / 'datahub-publication.json', result)
    emitter.close()
    return result


def main() -> int:
    """Publish a single stage from its immutable report, with an explicitly configured private GMS URL."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch')
    parser.add_argument('--stage', choices=STAGES)
    parser.add_argument('--bootstrap-user', action='store_true')
    args = parser.parse_args()
    endpoint = os.environ.get('DATAHUB_GMS_URL', 'http://datahub-gms:8080')
    if args.bootstrap_user:
        bootstrap_viewer(endpoint)
        return 0
    if not args.batch or not args.stage:
        parser.error('--batch and --stage are required for validation publication')
    result = publish(Path.cwd(), args.batch, args.stage, endpoint)
    print(json.dumps({'published': result['published'], 'assertions': len(result['assertions']), 'validation_passed': result['validation_passed']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
