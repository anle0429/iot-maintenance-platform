"""Reconcile stale Substrate worker addresses from actual owned Pod events without resetting actors or stored data."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
import argparse
import base64
import json
import os
import subprocess
import time

POOLS = {'iot-retrieval-pool', 'iot-drift-pool', 'iot-coordinator-pool'}


def run(root, output, restart_workers=False):
    """Record stale/current addresses, annotate only the six existing workers and require registry convergence."""
    root, output = root.resolve(), output.resolve()
    if os.name != 'posix' or os.geteuid() != 0 or not output.is_relative_to(root / 'outputs'):
        raise ValueError('Use sudo on the existing VM and a fresh project output')
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    if (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds() < 300:
        raise ValueError('Require five approved session minutes')
    active = subprocess.check_output(['systemctl', 'list-units', '--state=running', '--no-legend', 'iot-agent-chat-*'])
    if active.strip():
        raise ValueError('Preserve the active native conversation')
    k = ['/usr/local/bin/k3s', 'kubectl']; selector = 'ate.dev/worker-pool in (' + ','.join(sorted(POOLS)) + ')'
    raw = subprocess.check_output(k + ['get', 'pods', '-n', 'iot-agents', '-l', selector, '-o', 'json'])
    pods = json.loads(raw)['items']
    expected = {p['metadata']['name']: p['status']['podIP'] for p in pods}
    if len(expected) != 6 or any(sum(p['metadata']['labels']['ate.dev/worker-pool'] == pool for p in pods) != 2 for pool in POOLS):
        raise ValueError('Require exactly the six owned workers')
    if any(p['status']['phase'] != 'Running' or not all(c['ready'] for c in p['status'].get('containerStatuses', [])) for p in pods):
        raise ValueError('Preserve an unavailable worker for diagnosis')
    private = []
    for name in ['agent-platform.json', 'mcp-tools.json']:
        for value in json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values():
            private.extend([value.encode(), base64.b64encode(value.encode()), base64.b64encode(('Bearer ' + value).encode())])
    output.mkdir(exist_ok=False)

    def keep(name, body):
        """Refuse actual credentials before writing an original public candidate receipt."""
        if any(v in body for v in private):
            raise ValueError('Refuse a credential-bearing receipt')
        (output / name).write_bytes(body)

    keep('pods-before.json', raw); keep('source.py', Path(__file__).read_bytes())
    service = json.loads(subprocess.check_output(k + ['get', 'service', 'kagent-controller', '-n', 'iot-agents', '-o', 'json']))
    url = 'http://' + service['spec']['clusterIP'] + ':8083/api/substrate/status'
    opener = build_opener(ProxyHandler({}))

    def observe(name):
        """Read actual registered worker addresses through the private controller, preserving raw bytes."""
        with opener.open(url, timeout=30) as response: body = response.read()
        keep(name, body); data = json.loads(body)['data']
        if any(a['atespace'] == 'iot-agents' and a['status'] == 'Running' for a in data['actors']):
            raise ValueError('Wait for active native actors before replacing worker registrations')
        return {w['workerPod']: w['ip'] for w in data['workers'] if w['workerNamespace'] == 'iot-agents' and w['workerPool'] in POOLS}

    before = observe('registry-before.json'); report = {'passed': False, 'before': before, 'expected': expected}
    try:
        changed = before != expected
        if changed and restart_workers:
            # v0.0.9 retains the original IP when an existing Pod UID changes address after a VM reboot.
            # Replacing only owned worker Pods generates new registry identities; keep all volumes and actor receipts.
            for pool in sorted(POOLS):
                for phase, arguments in [('restart', ['rollout', 'restart', 'deployment/' + pool + '-deployment']),
                        ('ready', ['rollout', 'status', 'deployment/' + pool + '-deployment', '--timeout=180s'])]:
                    result = subprocess.run(k + arguments + ['-n', 'iot-agents'], capture_output=True, timeout=200)
                    keep(pool + '-' + phase + '.stdout', result.stdout)
                    keep(pool + '-' + phase + '.stderr', result.stderr)
                    keep(pool + '-' + phase + '.exit', (str(result.returncode) + '\n').encode())
                    if result.returncode: raise RuntimeError('Owned worker rollout failed: ' + pool)
            after_raw = subprocess.check_output(k + ['get', 'pods', '-n', 'iot-agents', '-l', selector, '-o', 'json'], timeout=30)
            keep('pods-after.json', after_raw)
            new_pods = [p for p in json.loads(after_raw)['items'] if not p['metadata'].get('deletionTimestamp')]
            expected = {p['metadata']['name']: p['status']['podIP'] for p in new_pods}
            if len(expected) != 6: raise ValueError('Require six replacement workers')
        elif changed:
            marker = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            result = subprocess.run(k + ['annotate', 'pods', '-n', 'iot-agents', '-l', selector,
                'iot.coursework/worker-address-refresh=' + marker, '--overwrite'], capture_output=True, timeout=30)
            keep('annotate.stdout', result.stdout); keep('annotate.stderr', result.stderr)
            keep('annotate.exit', (str(result.returncode) + '\n').encode())
            if result.returncode: raise RuntimeError('Owned Pod notification failed')
        deadline = time.monotonic() + 120; attempt = 0
        while True:
            attempt += 1; actual = observe(f'registry-after-{attempt}.json')
            if actual == expected: break
            if time.monotonic() >= deadline: raise TimeoutError('Worker addresses did not reconcile')
            time.sleep(3)
        report.update(passed=True, after=actual, expected_after=expected, annotation_applied=changed and not restart_workers,
            checked_utc=datetime.now(timezone.utc).isoformat(), source_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
            actors_reset=False, pods_restarted=changed and restart_workers,
            scope='Current six-worker registration addresses only. Actor routing and complete conversations require separate requests.')
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
    keep('verification.json', (json.dumps(report, indent=2) + '\n').encode())
    print(json.dumps(report, indent=2)); return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--restart-workers', action='store_true', help='Replace the six idle owned Pods when their retained registration addresses differ from actual Pod IPs')
    args = parser.parse_args(); raise SystemExit(run(args.runtime_root, args.output, args.restart_workers))
