"""Publish an outcome-verification span linked to a real A2A task's native trace."""

import argparse
import json
from pathlib import Path
import secrets
import time
from urllib.request import Request, urlopen


def batches(url):
    """Read Jaeger's streamed OTLP query response."""
    with urlopen(url, timeout=15) as response:
        return [json.loads(line)['result'] for line in response if line.strip()]


def attribute(key, value):
    """Build one OTLP text attribute."""
    return {'key': key, 'value': {'stringValue': value}}


def publish(query, ingest, native_trace, response):
    """Join the task to a stored SDK span before emitting a separate outcome trace."""
    start = time.time_ns()
    task = json.loads(response.read_bytes())['result']
    state = task['status']['state']
    if state not in {'completed', 'failed'}:
        raise ValueError('Expected a completed or failed A2A task')
    sources = batches(f'{query}/api/v3/traces/{native_trace}')
    matches = [
        span for batch in sources for resource in batch['resourceSpans']
        for scope in resource.get('scopeSpans', []) for span in scope.get('spans', [])
        if span['traceId'] == native_trace
        and attribute('gen_ai.task.id', task['id']) in span.get('attributes', [])
    ]
    if not matches:
        raise ValueError('The native trace does not contain the A2A task ID')
    trace_id = secrets.token_hex(16)
    span = {
        'traceId': trace_id, 'spanId': secrets.token_hex(8), 'name': f'Verify A2A outcome: {state}',
        'startTimeUnixNano': str(start), 'endTimeUnixNano': str(time.time_ns()),
        'status': {'code': 1 if state == 'completed' else 2},
        'attributes': [attribute('gen_ai.task.id', task['id']), attribute('a2a.task.state', state),
                       attribute('verification.source', 'stored A2A response')],
        'links': [{'traceId': native_trace, 'spanId': matches[0]['spanId']}],
    }
    resource = {'resource': {'attributes': [attribute('service.name', 'iot-a2a-outcome-verifier')]},
                'scopeSpans': [{'scope': {'name': 'iot.a2a.outcome'}, 'spans': [span]}]}
    request = Request(f'{ingest}/v1/traces', json.dumps({'resourceSpans': [resource]}).encode(),
                      {'Content-Type': 'application/json'})
    with urlopen(request, timeout=15) as result:
        if json.load(result).get('partialSuccess', {}).get('rejectedSpans'):
            raise ValueError('Jaeger rejected the verification span')
    time.sleep(2)
    stored = [s for batch in batches(f'{query}/api/v3/traces/{trace_id}')
              for resource in batch['resourceSpans'] for scope in resource['scopeSpans'] for s in scope['spans']]
    if (len(stored) != 1 or stored[0].get('status', {}).get('code') != span['status']['code']
            or stored[0].get('links', []) != span['links']
            or attribute('a2a.task.state', state) not in stored[0].get('attributes', [])):
        raise ValueError('Jaeger readback did not preserve the verification outcome')
    return {'verification_trace': trace_id, 'native_trace': native_trace, 'task': task['id'],
            'state': state, 'status_code': span['status']['code']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', required=True)
    parser.add_argument('--ingest', required=True)
    parser.add_argument('--native-trace', required=True)
    parser.add_argument('--response', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.query, args.ingest, args.native_trace, args.response)))
