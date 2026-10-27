"""Trigger and inspect the private Airflow materialization DAG, then verify indexed DataHub lineage/assertions."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DAG = 'iot_feast_materialization'


def api(path, method='GET', body=None, token=None):
    """Keep auth data in memory; never save tokens or response bodies from failed login requests."""
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = Request('http://127.0.0.1:18480' + path, method=method, headers=headers,
                      data=json.dumps(body).encode() if body is not None else None)
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as e:
        raise RuntimeError('Airflow HTTP ' + str(e.code)) from None


def login():
    """Use the VM's existing private learner account without printing credentials."""
    secret = json.loads((ROOT / 'data/runtime/secrets/orchestration-users.json').read_text())
    return api('/auth/token', 'POST', {'username': 'learner', 'password': secret['airflow']})['access_token']


def save(path, value):
    """Persist small API evidence only, excluding the in-memory authorization token."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def trigger(run, end):
    """Create a separate named DAG run; a one-slot SDK pool avoids concurrent writers."""
    token = login()
    try:
        api('/api/v2/pools', 'POST', {'name': 'feast_jobs', 'slots': 1, 'description': 'One bounded Feast SDK or metadata process', 'include_deferred': False}, token)
    except RuntimeError as e:
        if '409' not in str(e):
            raise
    api('/api/v2/dags/' + DAG, 'PATCH', {'is_paused': False}, token)
    result = api('/api/v2/dags/' + DAG + '/dagRuns', 'POST',
                 {'dag_run_id': run, 'logical_date': datetime.now(timezone.utc).isoformat(),
                  'conf': {'batch_id': run, 'end_utc': end}}, token)
    save(ROOT / 'outputs/feast-materialization' / (run + '-trigger.json'), result)
    print(json.dumps({'run': run, 'state': result['state']}))


def snapshot(run):
    """Read the actual scheduler state and every task instance; do not infer success from output existence."""
    token = login()
    base = '/api/v2/dags/' + DAG + '/dagRuns/' + run
    value = {'run': api(base, token=token), 'tasks': api(base + '/taskInstances', token=token),
             'checked_utc': datetime.now(timezone.utc).isoformat()}
    save(ROOT / 'outputs/feast-materialization' / (run + '-airflow.json'), value)
    print(json.dumps({'run': run, 'state': value['run']['state'], 'tasks': {x['task_id']: x['state'] for x in value['tasks']['task_instances']}}))
    return value


def metadata(run):
    """Check indexed compact/online lineage, the producing Airflow job and every assertion run result."""
    publication_path = ROOT / 'outputs/feast-materialization' / run / 'datahub-publication.json'
    publication = json.loads(publication_path.read_text())
    query = '''query($urn: String!) { dataset(urn: $urn) { urn name
      lineage(input: {direction: UPSTREAM, start: 0, count: 100}) { total relationships { entity { urn type } } }
      assertions(start: 0, count: 100) { assertions { urn
        runEvents(limit: 20) { runEvents { runId result { type } } } } }
    } }'''
    indexed = []
    for urn in (publication['input'], publication['output']):
        request = Request('http://127.0.0.1:18080/api/graphql', method='POST', headers={'Content-Type': 'application/json'},
                          data=json.dumps({'query': query, 'variables': {'urn': urn}}).encode())
        with urlopen(request, timeout=30) as response:
            value = json.load(response)
        if value.get('errors') or value['data']['dataset'] is None:
            raise ValueError('DataHub indexed dataset is not available yet')
        item = value['data']['dataset']
        upstreams = {x['entity']['urn'] for x in item['lineage']['relationships'] if x['entity']}
        expected = {publication['input'], publication['job']} if urn == publication['output'] else {publication['original_feature_input']}
        if not expected <= upstreams:
            raise ValueError('DataHub indexed table or producing job lineage is missing')
        if urn == publication['output']:
            definitions = {x['urn']: x for x in item['assertions']['assertions']}
            for assertion in publication['assertions']:
                if assertion['urn'] not in definitions or not any(e['runId'] == run and e['result']['type'] == ('SUCCESS' if assertion['passed'] else 'FAILURE')
                    for e in definitions[assertion['urn']]['runEvents']['runEvents']):
                    raise ValueError('DataHub indexed materialization assertion run is missing')
        indexed.append(item)
    proof = {'passed': True, 'run': run, 'datasets': indexed, 'checked_utc': datetime.now(timezone.utc).isoformat()}
    save(ROOT / 'outputs/feast-materialization' / (run + '-datahub.json'), proof)
    print(json.dumps({'run': run, 'indexed_datasets': len(indexed), 'assertions': len(publication['assertions'])}))
    return proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('trigger', 'snapshot', 'metadata'))
    parser.add_argument('--run', required=True)
    parser.add_argument('--end')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,63}', args.run):
        parser.error('Run name must use lowercase letters, digits and hyphens')
    if args.action == 'trigger':
        if not args.end:
            parser.error('--end is required for trigger')
        trigger(args.run, args.end)
    elif args.action == 'snapshot':
        snapshot(args.run)
    else:
        metadata(args.run)
