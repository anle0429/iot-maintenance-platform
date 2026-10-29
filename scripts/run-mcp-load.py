"""Run bounded, authenticated native Locust workloads and bind the reports to the actual Kubernetes MCP replicas."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import Request, urlopen
import argparse
import json
import os
import re
import subprocess


def run(root, source, run_name):
    """Preserve sealed source, readiness, original Locust HTML/CSV, metrics and a credential-free verification."""
    if os.name != 'posix' or os.geteuid() != 0 or not re.fullmatch(r'mcp-load-[a-z0-9-]+', run_name):
        raise ValueError('Use a distinct MCP load run on the retained Linux VM')
    root = root.resolve(); source = source.resolve()
    if not source.is_relative_to(root / 'data/llm-source'):
        raise ValueError('Use a source-sealed workload under the retained runtime')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    for name, digest in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Source differs from the seal')
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    if (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds() < 600:
        raise ValueError('Require at least ten minutes in the approved session')
    output = root / 'outputs' / run_name; output.mkdir(exist_ok=False)
    os.chown(output, 10001, 10001)
    (output / 'source.json').write_text(json.dumps(seal, indent=2) + '\n')
    k = ['/usr/local/bin/k3s', 'kubectl']

    def command(label, values, **kwargs):
        """Save original subprocess results without credential values or request headers."""
        result = subprocess.run(values, capture_output=True, timeout=180, **kwargs)
        (output / (label + '.stdout')).write_bytes(result.stdout)
        (output / (label + '.stderr')).write_bytes(result.stderr)
        (output / (label + '.exit')).write_text(str(result.returncode) + '\n')
        if result.returncode:
            raise RuntimeError(label + ' failed; inspect the original receipt')
        return result.stdout

    def resources(label):
        """Read only MCP deployment and pod objects, never Secret contents."""
        return json.loads(command(label, k + ['get', 'deployment,pod', '-n', 'iot-agents', '-o', 'json']))

    before = resources('resources-before')
    deployed = next(row for row in before['items'] if row['kind'] == 'Deployment' and row['metadata']['name'] == 'iot-retrieval-mcp')
    if deployed['status'].get('availableReplicas') != 2 or deployed['spec']['replicas'] != 2:
        raise ValueError('Require two genuinely ready retrieval replicas')
    service = json.loads(command('service', k + ['get', 'service', 'iot-retrieval-mcp', '-n', 'iot-agents', '-o', 'json']))
    base = 'http://' + service['spec']['clusterIP'] + ':8080'
    private = root / 'data/runtime/secrets' / (run_name + '.token')
    token = json.loads((root / 'data/runtime/secrets/mcp-tools.json').read_bytes())['retrieval']
    descriptor = os.open(private, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o400)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(token)
    os.chown(private, 10001, 10001)

    def request(path, authenticated=False):
        """Read real health, lineage and metrics without saving authorization headers."""
        headers = {'Authorization': 'Bearer ' + token} if authenticated else {}
        with urlopen(Request(base + path, headers=headers), timeout=10) as response:
            return response.read()

    try:
        ready = json.loads(request('/health/ready'))
        if ready.get('ready') is not True or ready.get('role') != 'retrieval' or not ready.get('pod'):
            raise ValueError('Actual MCP readiness differs')
        feature = json.loads(request('/v1/machines/P-001/features', True))
        (output / 'native-feature.json').write_text(json.dumps(feature, indent=2) + '\n')
        def metrics(label, objects):
            """Read each actual ready replica so load-balanced requests cannot mix counter identities."""
            total = 0; identities = []
            for pod in objects['items']:
                if pod['kind'] != 'Pod' or pod['metadata'].get('labels', {}).get('app.kubernetes.io/instance') != 'iot-retrieval-mcp':
                    continue
                if pod['metadata'].get('deletionTimestamp') or not any(row['type'] == 'Ready' and row['status'] == 'True' for row in pod['status']['conditions']):
                    continue
                identities.append(pod['metadata']['uid'])
                with urlopen('http://' + pod['status']['podIP'] + ':8080/metrics', timeout=10) as response:
                    raw = response.read()
                (output / (label + '-' + pod['metadata']['uid'] + '.prom')).write_bytes(raw)
                for line in raw.decode().splitlines():
                    if (line.startswith('iot_mcp_tool_calls_total{') and 'tool="get_machine_features"' in line
                            and 'result="success"' in line):
                        total += float(line.rsplit(' ', 1)[1])
            if len(identities) != 2:
                raise ValueError('Require two ready replicas for per-pod counter verification')
            return total, sorted(identities)

        calls_before, pods_before = metrics('metrics-before', before)
        for users in [1, 8]:
            prefix = output / ('users-' + str(users))
            env = {**os.environ, 'IOT_MCP_LOAD_TOKEN_FILE': str(private),
                   'IOT_MCP_LOAD_SOURCE_SHA256': feature['source_manifest_sha256'],
                   'IOT_MCP_LOAD_SUMMARY': str(prefix) + '-summary.json', 'HOME': '/tmp', 'PYTHONDONTWRITEBYTECODE': '1'}
            values = [str(root / 'data/runtime/locust-venv/bin/locust'), '-f', str(source / 'tests/load/mcp_retrieval.py'),
                      '--headless', '--host', base, '--users', str(users), '--spawn-rate', str(users), '--run-time', '30s',
                      '--stop-timeout', '5', '--only-summary', '--html', str(prefix) + '.html', '--csv', str(prefix)]
            (output / ('users-' + str(users) + '-command.json')).write_text(json.dumps(values, indent=2) + '\n')
            command('users-' + str(users), values, env=env, user=10001, group=10001)
            if not json.loads(Path(str(prefix) + '-summary.json').read_bytes())['passed']:
                raise ValueError('Declared load SLA failed')
        after = resources('resources-after')
        calls_after, pods_after = metrics('metrics-after', after)
        current = next(row for row in after['items'] if row['kind'] == 'Deployment' and row['metadata']['name'] == 'iot-retrieval-mcp')
        if current['metadata']['uid'] != deployed['metadata']['uid'] or current['spec'] != deployed['spec'] or current['status'].get('availableReplicas') != 2:
            raise ValueError('Deployment changed during the measured workload')
        measured = sum(json.loads((output / ('users-' + str(users) + '-summary.json')).read_bytes())['requests'] for users in [1, 8])
        if pods_before != pods_after or calls_after - calls_before != measured:
            raise ValueError('Actual per-replica successful calls differ from the load report')
        result = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'run': run_name,
                  'users': [1, 8], 'seconds_each': 30, 'replicas': 2,
                  'deployment_source_sha256': deployed['spec']['template']['metadata']['annotations']['iot.coursework/source-sha256'],
                  'per_replica_counter_delta': calls_after - calls_before, 'measured_requests': measured,
                  'feature_source_manifest_sha256': feature['source_manifest_sha256'], 'scope': 'Same shared VM; source-bound feature reads only.'}
        (output / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))
    finally:
        private.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--run', required=True)
    args = parser.parse_args(); run(args.runtime_root, args.source_root, args.run)
