"""Exercise an actual IAP-forwarded feature API and independently check model arithmetic, provenance, expiry and rejection cases."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
import argparse
import json
import math
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


class NoRedirect(HTTPRedirectHandler):
    """Keep this demo on the explicitly chosen local tunnel instead of following another endpoint."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Return ordinary non-success HTTP results to the caller without a redirected request."""
        return None


def aware(value):
    """Require explicit UTC offsets for both simulation time and the real preparation clock."""
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('A demo timestamp has no timezone')
    return result.astimezone(timezone.utc)


def check_prediction(trace, response, model, pin, now):
    """Recompute the score and refuse stale, future, leaked or differently pinned model responses."""
    if not 0 <= (now - aware(trace['prepared_wall_utc'])).total_seconds() < 3600:
        raise ValueError('Demo lease expired; perform a real materialization before demonstrating')
    asof = aware(trace['as_of']); event = aware(trace['feature_event_ts']); operating = trace['operating_context']
    if trace['clock_mode'] != 'simulation' or operating['status'] != 'operating':
        raise ValueError('Only operating machines have this prediction target')
    if not 0 <= (asof - event).total_seconds() <= 600 or aware(operating['received_ts']) > asof:
        raise ValueError('Feature time/arrival is unavailable at prediction time')
    if trace['protocol_sha256'] != pin['protocol_sha256'] or response['feature_trace'] != trace:
        raise ValueError('Feature/model trace differs')
    if any(response['model'].get(name) != value for name, value in pin.items()):
        raise ValueError('The API served a different model pin')
    vector = trace['features']
    if set(vector) != set(model['features']):
        raise ValueError('Unexpected feature schema or a leaked label')
    z = model['intercept'] + math.fsum(((vector[name] - mean) / scale if scale else 0) * coefficient
        for name, mean, scale, coefficient in zip(model['features'], model['mean'], model['std'], model['coefficients'], strict=True))
    expected = 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))
    actual = response['prediction']['risk_score']; decision = response['prediction']['predicted_failure']
    if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isfinite(actual) or abs(actual - expected) > 1e-12:
        raise ValueError('Native inference differs from independent portable-model arithmetic')
    if type(decision) is not int or decision != int(expected >= pin['threshold']):
        raise ValueError('Classification does not use the pinned threshold')
    return expected


def run(args):
    """Keep all nine HTTP responses before assertions and save a failed report when the actual runtime is unavailable."""
    parts = urlsplit(args.base_url)
    if parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost') or not parts.port or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/'):
        raise ValueError('Use an explicit loopback HTTP URL forwarded through IAP, for example http://127.0.0.1:18082')
    if any(not re.fullmatch(r'P-[0-9]{3,4}', value) for value in (args.operating, args.stopped)) or args.operating == args.stopped:
        raise ValueError('Choose distinct valid machine IDs for the operating and stopped examples')
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'outputs'):
        raise ValueError('Choose a new receipt directory under outputs/')
    output.mkdir(parents=True, exist_ok=False)
    pin = json.loads(args.pin.read_bytes()); raw_model = args.portable.read_bytes(); model = json.loads(raw_model)
    if sha256(raw_model).hexdigest() != pin['portable_sha256']:
        raise ValueError('Portable model bytes differ from the selected pin')
    replies = []; opener = build_opener(ProxyHandler({}), NoRedirect())
    def request(path, expected_status, post=False):
        """Call only preset safe demo routes and preserve bounded response bodies before checking their status."""
        req = Request(args.base_url.rstrip('/') + path, data=b'{}' if post else None, headers={'Content-Type': 'application/json'})
        try:
            response = opener.open(req, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read(2_097_153)
            if len(raw) > 2_097_152: raise ValueError('Demo response exceeds 2 MiB')
            item = {'path': path, 'method': 'POST' if post else 'GET', 'status': response.status, 'body': json.loads(raw)}
        replies.append(item); (output / 'http.json').write_text(json.dumps(replies, indent=2) + '\n', encoding='utf-8')
        if item['status'] != expected_status: raise ValueError(f'{path}: expected HTTP {expected_status}, received {item["status"]}')
        return item['body']
    report = {'passed': False, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_sha256': sha256(Path(__file__).read_bytes()).hexdigest(), 'scope': 'Actual private HTTP smoke test; not throughput, training quality or public gateway TLS verification.'}
    try:
        if request('/health/live', 200).get('live') is not True: raise ValueError('Process liveness is false')
        if request('/health/ready', 200).get('ready') is not True: raise ValueError('Native readiness is false')
        trace = request(f'/v1/machines/{args.operating}/features', 200)
        response = request(f'/v1/machines/{args.operating}:predict', 200, True)
        score = check_prediction(trace, response, model, pin, datetime.now(timezone.utc))
        if request(f'/v1/machines/{args.operating}:predict', 200, True) != response: raise ValueError('Prediction replay changed the result')
        stopped = request(f'/v1/machines/{args.stopped}/features', 200)
        if stopped['operating_context']['status'] not in ('failed', 'maintenance'): raise ValueError('The stopped demo machine is operating')
        request(f'/v1/machines/{args.stopped}:predict', 409, True)
        request('/v1/machines/P-9999/features', 404)
        request('/v1/machines/invalid/features', 422)
        report.update(passed=True, actual_http_requests=len(replies), operating=args.operating, stopped=args.stopped,
                      model_version=pin['version'], risk_score=score, prepared_wall_utc=trace['prepared_wall_utc'],
                      score_kind='uncalibrated_class_weighted_logistic', replay_identical=True)
    except Exception as error:
        report['error'] = str(error)
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:18082')
    parser.add_argument('--operating', default='P-001'); parser.add_argument('--stopped', default='P-006')
    parser.add_argument('--pin', type=Path, default=ROOT / 'docs/evidence/final-demo-linux/model-pin.json')
    parser.add_argument('--portable', type=Path, default=ROOT / 'docs/evidence/final-demo-linux/portable.json')
    parser.add_argument('--output', type=Path, required=True)
    sys.exit(run(parser.parse_args()))
