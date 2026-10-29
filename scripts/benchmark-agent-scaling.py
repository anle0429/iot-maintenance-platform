"""Generate real coordinator requests and observe demand-driven worker scale-out and idle scale-in."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener
import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time

ROLES = ['retrieval', 'drift', 'coordinator']


def retryable_capacity_error(reply):
    """Recognize only the native pre-dispatch capacity rejection, never an ambiguous transport or executed-task error."""
    error = reply.get('error', {})
    return (reply.get('result') is None and error.get('code') == -32603 and error.get('message') ==
            'failed to send HTTP request: Post "http://iot-coordinator.iot-agents:8080": '
            'substrate worker pool has no free workers; try again later or increase WorkerPool replicas')


def pool_metrics(pools, pods, actors):
    """Derive role occupancy and actual Ready capacity from original Kubernetes and Substrate observations."""
    result = {}
    for role in ROLES:
        name = 'iot-' + role + '-pool'; pool = next(p for p in pools if p['metadata']['name'] == name)
        selected = [p for p in pods if p['metadata']['labels'].get('ate.dev/worker-pool') == name]
        ready = [p for p in selected if not p['metadata'].get('deletionTimestamp')
                 and any(c['type'] == 'Ready' and c['status'] == 'True' for c in p['status'].get('conditions', []))]
        running = [a for a in actors if a['atespace'] == 'iot-agents' and a['status'] == 'Running'
                   and a['actorTemplateName'].startswith('iot-' + role + '-')]
        result[role] = {'desired': pool['spec']['replicas'], 'reported': pool['status']['replicas'], 'ready': len(ready),
                        'running': len(running), 'ready_worker_uids': [p['metadata']['uid'] for p in ready],
                        'active_worker_names': sorted({a['ateomPodName'] for a in running})}
    return result


def run(root, source, output, duration, feature_run):
    """Keep all request/child traces, adapt concurrency to observed capacity and require scale-in after work finishes."""
    root, source, output = root.resolve(), source.resolve(), output.resolve()
    if (os.geteuid() != 0 or not source.is_relative_to(root / 'data/llm-source')
            or not output.is_relative_to(root / 'outputs') or not 90 <= duration <= 180):
        raise ValueError('Require sudo, sealed project source, a fresh project output and a bounded burst')
    now = datetime.now(timezone.utc); session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    lease_path = (root / 'outputs' / feature_run / 'verification.json').resolve()
    if not lease_path.is_relative_to(root / 'outputs'): raise ValueError('Require an owned genuine feature-preparation report')
    lease = json.loads(lease_path.read_bytes())
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))) * 1024
    if (not lease['passed'] or lease['machines'] != 200 or datetime.fromisoformat(lease['prepared_wall_utc']) > now
            or (datetime.fromisoformat(lease['lease_expires_utc']) - now).total_seconds() < 1200
            or (datetime.fromisoformat(session['auto_stop_utc']) - now).total_seconds() < 1800
            or available < 12 * 1024**3 or shutil.disk_usage('/srv/iot-fast').free < 20 * 1024**3):
        raise ValueError('Require genuinely fresh features and enough session/RAM/SSD before the bounded workload')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    from hashlib import sha256
    for name, digest in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != digest: raise ValueError('Source changed')
    output.mkdir(exist_ok=False); os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'
    (output / 'source.json').write_text(json.dumps(seal, indent=2) + '\n')
    (output / 'feature-lease.json').write_bytes(lease_path.read_bytes())
    sys.path.insert(0, str(source))
    from agents.delegation_acceptance import verify_coordinator
    from agents.probe import request as a2a_request
    private = []
    for name in ['agent-platform.json', 'mcp-tools.json']:
        for value in json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values(): private.extend([value.encode(), base64.b64encode(value.encode())])

    def keep(name, raw):
        """Keep original measurement bytes after scanning actual runtime credential values."""
        if any(secret in raw for secret in private): raise ValueError('Credential-bearing receipt refused')
        path = output / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)

    def kube(args):
        """Read actual bounded Kubernetes JSON without changing replica counts."""
        return subprocess.check_output(['/usr/local/bin/k3s', 'kubectl'] + args, timeout=20)

    opener = build_opener(ProxyHandler({})); service = json.loads(kube(['get', 'service', 'kagent-controller', '-n', 'iot-agents', '-o', 'json']))
    base = 'http://' + service['spec']['clusterIP'] + ':8083'; history = []

    def sample():
        """Retain each original input and independently derived observation before considering scale acceptance."""
        number = len(history); prefix = 'observations/' + str(number).zfill(4)
        raw_pools = kube(['get', 'workerpools', '-n', 'iot-agents', '-o', 'json'])
        raw_pods = kube(['get', 'pods', '-n', 'iot-agents', '-o', 'json'])
        raw_hpa = kube(['get', 'hpa', '-n', 'iot-agents', '-o', 'json'])
        raw_scalers = kube(['get', 'scaledobjects', '-n', 'iot-agents', '-o', 'json'])
        with opener.open(base + '/api/substrate/status', timeout=15) as response: raw_actors = response.read()
        for name, raw in [('pools', raw_pools), ('pods', raw_pods), ('hpa', raw_hpa), ('scalers', raw_scalers), ('actors', raw_actors)]: keep(prefix + '/' + name + '.json', raw)
        metrics = pool_metrics(json.loads(raw_pools)['items'], json.loads(raw_pods)['items'], json.loads(raw_actors)['data']['actors'])
        row = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'elapsed_seconds': time.monotonic() - start, 'roles': metrics, 'prefix': prefix}
        history.append(row); keep('observations.json', (json.dumps(history, indent=2) + '\n').encode())
        return row

    start = time.monotonic(); initial = sample()
    if any(v['desired'] != 2 or v['ready'] != 2 or v['running'] != 0 for v in initial['roles'].values()):
        raise ValueError('Begin only from two idle Ready workers per role')
    stop = threading.Event(); deadline = time.monotonic() + duration; results = []; lock = threading.Lock()

    def client(client_id):
        """Send complete real coordinator questions and verify both actual delegated child histories."""
        count = 0; local = build_opener(ProxyHandler({}))
        while time.monotonic() < deadline and not stop.is_set():
            count += 1; name = 'requests/client-' + str(client_id) + '-' + str(count); request_out = output / name
            args = [sys.executable, '-B', str(source / 'agents/probe.py'), '--base-url', base, '--output', str(request_out),
                    '--roles', 'coordinator', '--coordinator-timeout', '300']
            completed = subprocess.run(args, capture_output=True, timeout=330)
            keep(name + '/client.stdout', completed.stdout); keep(name + '/client.stderr', completed.stderr)
            keep(name + '/client.exit', (str(completed.returncode) + '\n').encode())
            response_path = request_out / 'coordinator-response.json'
            if not response_path.exists(): stop.set(); raise ValueError('Ambiguous transport failure is not automatically retried')
            reply = json.loads(response_path.read_bytes()); children = {}; retries = 0
            identity = json.loads((request_out / 'identity.json').read_bytes())['sub']
            if completed.returncode and not retryable_capacity_error(reply):
                stop.set(); raise ValueError('An executed-task or unrelated RPC failure is not a capacity retry')
            original_request = json.loads((request_out / 'coordinator-request.json').read_bytes())
            retry_deadline = time.monotonic() + 60
            while retryable_capacity_error(reply):
                if time.monotonic() >= retry_deadline: stop.set(); raise TimeoutError('Capacity backpressure did not clear within sixty seconds')
                time.sleep(1); retries += 1; retry_prefix = name + '/capacity-retries/' + str(retries).zfill(3)
                keep(retry_prefix + '/request.json', json.dumps(original_request).encode())
                status, raw, elapsed = a2a_request(local, base + '/api/a2a-sandboxes/iot-agents/iot-coordinator', original_request, user_id=identity, timeout=300)
                keep(retry_prefix + '/response.json', raw)
                keep(retry_prefix + '/transport.json', (json.dumps({'http_status': status, 'elapsed_seconds': elapsed, 'response_complete': True}, indent=2) + '\n').encode())
                reply = json.loads(raw); response_path = output / retry_prefix / 'response.json'
                if status != 200 or (reply.get('error') and not retryable_capacity_error(reply)):
                    stop.set(); raise ValueError('Unexpected retry result; preserve the original response')
            if reply['result']['contextId'] != original_request['params']['message']['contextId']:
                stop.set(); raise ValueError('A retry changed the original A2A context')
            for message in reply['result']['history']:
                for part in message.get('parts', []):
                    if part.get('metadata', {}).get('adk_type') != 'function_response': continue
                    data = part['data']; role = {'iot_agents__NS__iot_retrieval': 'retrieval', 'iot_agents__NS__iot_drift': 'drift'}.get(data['name'])
                    if not role: continue
                    session = data['response']['subagent_session_id']; children[role] = {}
                    for kind, suffix in [('session', ''), ('tasks', '/tasks')]:
                        req = Request(base + '/api/sessions/' + session + suffix, headers={'X-User-Id': identity})
                        with local.open(req, timeout=20) as response: raw = response.read()
                        keep(name + '/' + role + '-' + kind + '.json', raw); children[role][kind] = json.loads(raw)
            accepted = verify_coordinator(reply, children, knowledge_root=source / 'knowledge')
            keep(name + '/acceptance.json', (json.dumps(accepted, indent=2) + '\n').encode())
            with lock: results.append({'path': name, 'passed': accepted['passed'], 'capacity_rejections': retries,
                                       'http_attempts': 1 + retries, 'final_response_path': response_path.relative_to(output).as_posix()})
        return count

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(client, i) for i in range(2)]; expanded = False
        while any(not future.done() for future in futures):
            row = sample()
            if not expanded and row['roles']['coordinator']['ready'] >= 4:
                futures += [pool.submit(client, i) for i in [2, 3]]; expanded = True
            if stop.is_set(): break
            time.sleep(2)
        for future in futures: future.result()
    keep('requests.json', (json.dumps(results, indent=2) + '\n').encode())
    end_deadline = time.monotonic() + 420
    while True:
        row = sample()
        if all(v['desired'] == 2 and v['ready'] == 2 and v['running'] == 0 for v in row['roles'].values()): break
        if time.monotonic() >= end_deadline: raise TimeoutError('Automatic scale-in was not observed; do not manually scale to pass')
        time.sleep(5)
    maxima = {role: {field: max(row['roles'][role][field] for row in history) for field in ['desired', 'ready', 'running']} for role in ROLES}
    passed = bool(results) and all(v['desired'] >= 3 and v['ready'] >= 3 and v['running'] >= 2 for v in maxima.values())
    report = {'passed': passed, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'verified_coordinator_requests': len(results),
              'configured_burst_seconds': duration, 'observed_seconds': time.monotonic() - start, 'maxima': maxima,
              'automatic_scale_in': True, 'manual_replica_changes': False, 'coursework_complete': False,
              'retryable_capacity_rejections': sum(r['capacity_rejections'] for r in results), 'unhandled_request_failures': 0,
              'scope': 'Real coordinator/child workload and worker-capacity elasticity on one control VM. Shared GPU throughput and cross-node HA are separate claims.'}
    keep('verification.json', (json.dumps(report, indent=2) + '\n').encode()); print(json.dumps(report, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True); parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True); parser.add_argument('--duration', type=int, default=120)
    parser.add_argument('--feature-run', required=True)
    args = parser.parse_args(); raise SystemExit(run(args.runtime_root, args.source_root, args.output, args.duration, args.feature_run))
