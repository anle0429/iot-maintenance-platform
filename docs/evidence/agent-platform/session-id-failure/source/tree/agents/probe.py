"""Call the deployed kagent A2A endpoint and preserve actual replies, identifiers and elapsed time."""
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener
from uuid import uuid4
import argparse
import json
import time


QUESTIONS = {
    'retrieval': 'For pump P-001, fetch the current machine features and failure prediction. Use the knowledge tool to explain whether high vibration alone proves failure. Include the actual event timestamp, model version and cited document IDs.',
    'drift': 'Assess feature drift for the standard demo cohort of all 32 unique machines P-001 through P-032 with window_id agent-demo-29. Report actual unique_rows, flagged features, reference_id and assessment_sha256.',
    'coordinator': 'Use both specialists. Get the current features and failure prediction for pump P-001 and a reference about vibration. Also assess drift for the standard demo cohort of all 32 unique machines P-001 through P-032 using window_id agent-demo-29. Combine the actual results, preserving model and source identifiers.',
}


def request(opener, url, payload=None):
    """Send a native A2A/HTTP request with a fixed body length and return the original response bytes."""
    headers = {'Accept': 'application/json', 'A2A-Version': '0.3'}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode(); headers['Content-Type'] = 'application/json'
    started = time.monotonic()
    try:
        with opener.open(Request(url, data=data, headers=headers), timeout=600) as response:
            return response.status, response.read(), time.monotonic() - started
    except HTTPError as error:
        return error.code, error.read(), time.monotonic() - started


def run(base_url, output, roles):
    """Check protocol completion without treating fluent model text as proof of actual downstream tool execution."""
    output.mkdir(parents=True, exist_ok=False)
    opener = build_opener(ProxyHandler({})); reports = []
    for role in roles:
        url = base_url.rstrip('/') + '/api/a2a-sandboxes/iot-agents/iot-' + role
        status, body, elapsed = request(opener, url + '/.well-known/agent-card.json')
        (output / (role + '-card.json')).write_bytes(body)
        if status != 200:
            raise ValueError('Agent card failed for ' + role + ': HTTP ' + str(status))
        payload = {'jsonrpc': '2.0', 'id': str(uuid4()), 'method': 'message/send',
            'params': {'message': {'role': 'user', 'messageId': str(uuid4()), 'parts': [{'kind': 'text', 'text': QUESTIONS[role]}]},
                       'configuration': {'blocking': True}}}
        (output / (role + '-request.json')).write_text(json.dumps(payload, indent=2) + '\n')
        status, body, elapsed = request(opener, url, payload)
        (output / (role + '-response.json')).write_bytes(body)
        (output / (role + '-transport.json')).write_text(json.dumps({'http_status': status, 'elapsed_seconds': elapsed,
            'response_bytes': len(body), 'requested_a2a_version': '0.3'}, indent=2) + '\n')
        try:
            reply = json.loads(body)
        except json.JSONDecodeError as error:
            raise ValueError('Native A2A returned a non-JSON body: HTTP ' + str(status)) from error
        result = reply.get('result', {})
        state = result.get('status', {}).get('state')
        report = {'role': role, 'http_status': status, 'elapsed_seconds': elapsed, 'a2a_state': state,
                  'task_id': result.get('id'), 'context_id': result.get('contextId'), 'rpc_error': reply.get('error')}
        reports.append(report)
        (output / 'results.json').write_text(json.dumps(reports, indent=2) + '\n')
        if status != 200 or reply.get('error') or state not in ['completed', 'input-required']:
            raise ValueError('Native A2A request did not complete: ' + role)
    summary = {'passed': all(r['a2a_state'] == 'completed' for r in reports), 'calls': reports,
               'scope': 'Actual A2A protocol completion and original text. Tool execution, factual correctness and delegation require independent event/tool receipts.'}
    (output / 'verification.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2)); return 0 if summary['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--roles', nargs='+', choices=list(QUESTIONS), default=list(QUESTIONS))
    args = parser.parse_args(); raise SystemExit(run(args.base_url, args.output, args.roles))
