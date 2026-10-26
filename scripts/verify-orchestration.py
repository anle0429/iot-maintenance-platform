"""Prepare the pilot, trigger real Airflow DAG runs and collect bounded API evidence without logging credentials."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generator.chunked import ChunkConfig, generate_chunked
from pipelines.chunk_source import ChunkSource, validation_receipt
from pipelines.orchestration_contract import save, stage_path, utc_now

DAGS = ['iot_dp1_bronze', 'iot_dp2_warehouse', 'iot_dp3_features']


def api(path: str, method='GET', body=None, token=None):
    """Use only VM loopback; access tokens remain in memory and never enter evidence."""
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = Request('http://127.0.0.1:18480' + path, data=json.dumps(body).encode() if body is not None else None,
                      headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f'Airflow HTTP {error.code}: {error.read().decode()[:1000]}') from None


def login() -> str:
    """Authenticate with the preseeded local-only learner account."""
    passwords = json.loads((ROOT / 'data/runtime/secrets/orchestration-users.json').read_text())
    return api('/auth/token', 'POST', {'username': 'learner', 'password': passwords['airflow']})['access_token']


def prepare() -> None:
    """Generate a continuous 20-machine pilot and bind its complete semantic validation receipt."""
    source = ROOT / 'data/source/orchestration-pilot'
    receipt = ROOT / 'outputs/orchestration/source-receipt.json'
    if not source.exists():
        generate_chunked(ChunkConfig.load(ROOT / 'generator/configs/orchestration-pilot.json'), source,
                         chunk_rows=10_000, include_stream=False)
    if not receipt.exists():
        validation_receipt(source, receipt)
    actual = ChunkSource(source, receipt)
    print(json.dumps({'source_validated': True, 'offline_bytes': actual.offline_bytes, 'unique_rows': actual.config.unique_rows}))


def health() -> dict:
    """Return service health, DAG import errors and resource limits, excluding container environment secrets."""
    token = login()
    result = {'checked_utc': utc_now(), 'airflow': api('/api/v2/monitor/health'),
              'dags': api('/api/v2/dags?limit=20', token=token),
              'import_errors': api('/api/v2/importErrors?limit=20', token=token)}
    with urlopen('http://127.0.0.1:18080/health', timeout=20) as response:
        result['datahub_gms_health'] = response.read().decode()
    ids = subprocess.check_output(['docker', 'ps', '-aq'], text=True).split()
    details = json.loads(subprocess.check_output(['docker', 'inspect', *ids], text=True)) if ids else []
    result['containers'] = [{'name': item['Name'], 'image': item['Config']['Image'], 'image_id': item['Image'],
                             'state': {key: item['State'].get(key) for key in ('Status', 'OOMKilled', 'ExitCode', 'StartedAt', 'FinishedAt')},
                             'health': item['State'].get('Health', {}).get('Status'), 'memory_limit': item['HostConfig']['Memory'],
                             'nano_cpus': item['HostConfig']['NanoCpus'], 'ports': item['NetworkSettings']['Ports']}
                            for item in details]
    save(ROOT / 'outputs/orchestration/health.json', result)
    print(json.dumps({'dags': [d['dag_id'] for d in result['dags']['dags']], 'import_errors': result['import_errors']['total_entries'],
                      'gms': result['datahub_gms_health']}))
    return result


def trigger(batch: str, fault: str) -> None:
    """Start DP1 through the scheduler; DAG dependencies, not this script, release DP2 and DP3."""
    stage_path(ROOT, batch, 'dp1')
    token = login()
    # A one-slot pool prevents two memory-heavy Spark stages from competing on the VM.
    try:
        api('/api/v2/pools', 'POST', {'name': 'spark_jobs', 'slots': 1, 'description': 'Single bounded Spark job on the coursework VM', 'include_deferred': False}, token)
    except RuntimeError as error:
        if '409' not in str(error):
            raise
    for dag in DAGS:
        api(f'/api/v2/dags/{dag}', 'PATCH', {'is_paused': False}, token)
    result = api(f'/api/v2/dags/{DAGS[0]}/dagRuns', 'POST',
                 {'dag_run_id': batch, 'logical_date': datetime.now(timezone.utc).isoformat(),
                  'conf': {'batch_id': batch, 'fault': fault}}, token)
    save(ROOT / f'outputs/orchestration/{batch}-trigger.json', result)
    print(json.dumps({'triggered': batch, 'state': result['state']}))


def snapshot(batch: str) -> dict:
    """Read scheduler states and all task instances without treating a missing downstream run as success."""
    token = login()
    result = {'batch': batch, 'checked_utc': utc_now(), 'dags': {}}
    for dag in DAGS:
        try:
            run = api(f'/api/v2/dags/{dag}/dagRuns/{batch}', token=token)
            tasks = api(f'/api/v2/dags/{dag}/dagRuns/{batch}/taskInstances', token=token)
            result['dags'][dag] = {'run': run, 'tasks': tasks}
        except RuntimeError as error:
            if '404' not in str(error):
                raise
            result['dags'][dag] = {'run': None, 'tasks': None}
    save(ROOT / f'outputs/orchestration/{batch}-airflow.json', result)
    print(json.dumps({dag: {'state': item['run']['state'] if item['run'] else 'not_created',
                           'tasks': {t['task_id']: t['state'] for t in item['tasks']['task_instances']} if item['tasks'] else {}}
                      for dag, item in result['dags'].items()}))
    return result


def metadata(batch: str) -> dict:
    """Check indexed dataset/job lineage and the exact reported assertion run results through GraphQL."""
    query = '''query($urn: String!) { dataset(urn: $urn) { urn name
      lineage(input: {direction: UPSTREAM, start: 0, count: 100}) { total relationships { type entity { urn type } } }
      assertions(start: 0, count: 100) { total assertions { urn info { type description }
        runEvents(limit: 20) { total failed succeeded runEvents { runId timestampMillis result { type } } } } }
    } }'''
    datasets = []
    for stage in ('dp1', 'dp2', 'dp3'):
        publication = stage_path(ROOT, batch, stage) / 'datahub-publication.json'
        if not publication.exists():
            continue
        published = json.loads(publication.read_text())
        for dataset in published['datasets_read_back']:
            req = Request('http://127.0.0.1:18080/api/graphql', method='POST', headers={'Content-Type': 'application/json'},
                          data=json.dumps({'query': query, 'variables': {'urn': dataset['dataset']}}).encode())
            with urlopen(req, timeout=30) as response:
                value = json.load(response)
            if value.get('errors'):
                raise ValueError(f'GraphQL readback errors: {value["errors"]}')
            actual = value['data']['dataset']
            if actual is None:
                raise ValueError('Dataset missing from GraphQL')
            upstreams = {edge['entity']['urn'] for edge in actual['lineage']['relationships'] if edge['entity']}
            if not set(dataset['upstreams']).issubset(upstreams):
                raise ValueError('Table lineage has not reached the search/graph index')
            # Producing-job edges must also be queryable, not merely acknowledged in primary storage.
            if published['job'] not in upstreams:
                raise ValueError('Producing Airflow job missing from indexed dataset lineage')
            definitions = {item['urn']: item for item in actual['assertions']['assertions']}
            for expected in [item for item in published['assertions'] if item['dataset'] == dataset['dataset']]:
                assertion = definitions.get(expected['urn'])
                if not assertion:
                    raise ValueError('Assertion not indexed on target dataset')
                events = assertion['runEvents']['runEvents']
                if not any(event['runId'] == batch and event['result']['type'] == expected['result'] for event in events):
                    raise ValueError('Assertion run result missing or differs from the Spark validation report')
            datasets.append(actual)
    if not datasets:
        raise ValueError('No DataHub publication for this batch')
    result = {'passed': True, 'batch': batch, 'checked_utc': utc_now(), 'datasets': datasets}
    save(ROOT / f'outputs/orchestration/{batch}-datahub.json', result)
    print(json.dumps({'indexed_datasets_verified': len(datasets), 'batch': batch}))
    return result


def main() -> None:
    """Keep preparation, triggering and inspection separate so failed runs remain reviewable."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'health', 'trigger', 'snapshot', 'metadata'))
    parser.add_argument('--batch', default='orchestration-good-26')
    parser.add_argument('--fault', choices=('', 'null_machine'), default='')
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.action == 'prepare':
        prepare()
    elif args.action == 'health':
        health()
    elif args.action == 'trigger':
        trigger(args.batch, args.fault)
    elif args.action == 'metadata':
        metadata(args.batch)
    else:
        snapshot(args.batch)


if __name__ == '__main__':
    main()
