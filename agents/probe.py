"""Call the deployed kagent A2A endpoint and preserve actual replies, identifiers and elapsed time."""
from pathlib import Path
from http.client import IncompleteRead
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


class TransportFailure(RuntimeError):
    """Carry observed transport facts without inventing an HTTP response or disclosing exception text."""

    def __init__(self, error_type, elapsed, status=None, partial=b''):
        """Retain partial response bytes and any actually observed status for the caller's failed-run receipts."""
        super().__init__('Native HTTP transport did not complete: ' + error_type)
        self.error_type = error_type; self.elapsed = elapsed; self.status = status; self.partial = partial


def request(opener, url, payload=None, user_id=None, timeout=30):
    """Send a native A2A/HTTP request with a fixed body length and return the original response bytes."""
    headers = {'Accept': 'application/json', 'A2A-Version': '0.3'}
    if user_id:
        headers['X-User-Id'] = user_id
    data = None
    if payload is not None:
        data = json.dumps(payload).encode(); headers['Content-Type'] = 'application/json'
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 2400:
        raise ValueError('Use a bounded HTTP timeout of at most 2400 seconds')
    started = time.monotonic(); status = None
    try:
        with opener.open(Request(url, data=data, headers=headers), timeout=timeout) as response:
            status = response.status
            return response.status, response.read(), time.monotonic() - started
    except HTTPError as error:
        try:
            body = error.read()
        except (OSError, IncompleteRead) as read_error:
            raise TransportFailure(type(read_error).__name__, time.monotonic() - started,
                                   error.code, getattr(read_error, 'partial', b'')) from read_error
        return error.code, body, time.monotonic() - started
    except (OSError, IncompleteRead) as error:
        raise TransportFailure(type(error).__name__, time.monotonic() - started,
                               status, getattr(error, 'partial', b'')) from error


def run(base_url, output, roles, specialist_timeout=1200, coordinator_timeout=2100):
    """Check protocol completion without treating fluent model text as proof of actual downstream tool execution."""
    output.mkdir(parents=True, exist_ok=False)
    opener = build_opener(ProxyHandler({})); reports = []
    status, body, _ = request(opener, base_url.rstrip('/') + '/api/me')
    (output / 'identity.json').write_bytes(body)
    identity = json.loads(body).get('sub')
    if status != 200 or not identity:
        raise ValueError('The private controller did not report a current test identity')
    for role in roles:
        url = base_url.rstrip('/') + '/api/a2a-sandboxes/iot-agents/iot-' + role
        status, body, elapsed = request(opener, url + '/.well-known/agent-card.json', user_id=identity)
        (output / (role + '-card.json')).write_bytes(body)
        if status != 200:
            raise ValueError('Agent card failed for ' + role + ': HTTP ' + str(status))
        payload = {'jsonrpc': '2.0', 'id': str(uuid4()), 'method': 'message/send',
            'params': {'message': {'role': 'user', 'messageId': str(uuid4()), 'contextId': str(uuid4()),
                                  'parts': [{'kind': 'text', 'text': QUESTIONS[role]}]},
                       'configuration': {'blocking': True}}}
        (output / (role + '-request.json')).write_text(json.dumps(payload, indent=2) + '\n')
        timeout = coordinator_timeout if role == 'coordinator' else specialist_timeout
        try:
            status, body, elapsed = request(opener, url, payload, user_id=identity, timeout=timeout)
        except TransportFailure as error:
            if error.partial:
                (output / (role + '-response.partial')).write_bytes(error.partial)
            transport = {'http_status': error.status, 'elapsed_seconds': error.elapsed,
                'response_bytes': len(error.partial), 'requested_a2a_version': '0.3',
                'response_complete': False, 'error_type': error.error_type, 'timeout_seconds': timeout}
            (output / (role + '-transport.json')).write_text(json.dumps(transport, indent=2) + '\n')
            reports.append({'role': role, 'http_status': error.status, 'a2a_state': None,
                            'transport_error': error.error_type, 'elapsed_seconds': error.elapsed})
            (output / 'results.json').write_text(json.dumps(reports, indent=2) + '\n')
            raise ValueError('Native A2A transport did not complete: ' + role) from error
        (output / (role + '-response.json')).write_bytes(body)
        (output / (role + '-transport.json')).write_text(json.dumps({'http_status': status, 'elapsed_seconds': elapsed,
            'response_bytes': len(body), 'requested_a2a_version': '0.3', 'response_complete': True,
            'timeout_seconds': timeout}, indent=2) + '\n')
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
    parser.add_argument('--specialist-timeout', type=float, default=1200)
    parser.add_argument('--coordinator-timeout', type=float, default=2100)
    args = parser.parse_args(); raise SystemExit(run(args.base_url, args.output, args.roles, args.specialist_timeout, args.coordinator_timeout))
