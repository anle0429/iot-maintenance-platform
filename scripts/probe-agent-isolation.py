"""Measure filesystem and role-specific egress boundaries inside an existing owned gVisor actor."""
from hashlib import sha256
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
import argparse
import base64
import json
import os
import subprocess

BINARY_SHA256 = 'd3c2ccda96495a6e33f877bffb3c88be24136cd2f638c2aa625406fa42449f8b'


def run(root, request_file, role, binary, output):
    """Inject only the fixed read-only diagnostic, correlating its actor with the actual A2A request and worker."""
    root, request_file, binary, output = [p.resolve() for p in [root, request_file, binary, output]]
    if (os.geteuid() != 0 or role not in {'retrieval', 'drift'} or not request_file.is_relative_to(root / 'outputs')
            or request_file.name != role + '-request.json' or not output.is_relative_to(root / 'outputs')):
        raise ValueError('Use an owned specialist request and fresh project output with sudo')
    session = json.loads(request_file.read_bytes())['params']['message']['contextId']
    candidate = 'asr-iot-agents-iot-' + role + '-' + session.strip().lower().replace('_', '-')
    actor_id = candidate if len(candidate) <= 63 else 'asr-' + sha256(('iot-agents/iot-' + role + '/' + session).encode()).hexdigest()[:24]
    raw = binary.read_bytes()
    if raw[:4] != b'\x7fELF' or sha256(raw).hexdigest() != BINARY_SHA256:
        raise ValueError('Require the source-recorded static diagnostic binary')
    canonical = Path('/var/lib/ateom-gvisor'); backing = Path('/srv/iot-fast/ateom-gvisor')
    if (canonical.stat().st_dev, canonical.stat().st_ino) != (backing.stat().st_dev, backing.stat().st_ino):
        raise ValueError('Require the existing owned SSD bind mount')
    runtime = canonical / 'static-files/runsc-f18a948bf9c8bbb54eb998549a3a8d719a1c7de2efbe8fdd2ff0ee5fecd06f19'
    if sha256(runtime.read_bytes()).hexdigest() != runtime.name.removeprefix('runsc-'):
        raise ValueError('The actual gVisor runtime differs')
    state = canonical / 'actors' / ('iot-agents:' + actor_id) / 'runsc-state'
    if not state.is_dir(): raise ValueError('The native actor state directory is absent')
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'
    k = ['/usr/local/bin/k3s', 'kubectl']; opener = build_opener(ProxyHandler({})); private = []
    for name in ['agent-platform.json', 'mcp-tools.json']:
        for value in json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values():
            private.extend([value.encode(), base64.b64encode(value.encode()), base64.b64encode(('Bearer ' + value).encode())])
    service = json.loads(subprocess.check_output(k + ['get', 'service', 'kagent-controller', '-n', 'iot-agents', '-o', 'json']))
    with opener.open('http://' + service['spec']['clusterIP'] + ':8083/api/substrate/status', timeout=20) as response:
        inventory = json.load(response)['data']
    actors = [a for a in inventory['actors'] if a['actorId'] == actor_id and a['atespace'] == 'iot-agents' and a['status'] == 'Running']
    if len(actors) != 1 or actors[0]['ateomPodNamespace'] != 'iot-agents':
        raise ValueError('Require one observed Running actor in its owned worker')
    worker = json.loads(subprocess.check_output(k + ['get', 'pod', actors[0]['ateomPodName'], '-n', 'iot-agents', '-o', 'json']))
    if worker['metadata']['labels'].get('ate.dev/worker-pool') != 'iot-' + role + '-pool' or worker['status']['phase'] != 'Running':
        raise ValueError('The observed actor is not in its corresponding native worker pool')
    output.mkdir(exist_ok=False)

    def keep(name, body):
        """Retain original diagnostic bytes only after scanning actual credential values and encodings."""
        if any(secret in body for secret in private): raise ValueError('Refuse a credential-bearing diagnostic receipt')
        (output / name).write_bytes(body)

    keep('native-request.json', request_file.read_bytes())
    keep('actor.json', (json.dumps(actors[0], indent=2) + '\n').encode())
    keep('worker.json', (json.dumps(worker, indent=2) + '\n').encode())
    positives = {}
    for selected in ['retrieval', 'drift']:
        svc = json.loads(subprocess.check_output(k + ['get', 'service', 'iot-' + selected + '-mcp', '-n', 'iot-agents', '-o', 'json']))
        with opener.open('http://' + svc['spec']['clusterIP'] + ':8080/health/live', timeout=10) as response:
            positives[selected] = response.status
    if positives != {'retrieval': 200, 'drift': 200}: raise ValueError('Both native host-side positive controls must work')
    target = canonical / 'static-files/iot-sandbox-probe-29a'
    if target.exists():
        if sha256(target.read_bytes()).hexdigest() != BINARY_SHA256: raise ValueError('Preserve a different diagnostic file')
    else:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
        with os.fdopen(descriptor, 'wb') as destination: destination.write(raw)
    argv = k + ['exec', '-n', 'iot-agents', worker['metadata']['name'], '--', str(target), '--inject', '--state-root=' + str(state), '--role=' + role]
    result = subprocess.run(argv, capture_output=True, timeout=45)
    keep('command.json', (json.dumps(argv, indent=2) + '\n').encode())
    keep('diagnostic.stdout', result.stdout); keep('diagnostic.stderr', result.stderr)
    keep('diagnostic.exit', (str(result.returncode) + '\n').encode())
    measured = json.loads(result.stdout) if result.returncode == 0 else None
    report = {'passed': result.returncode == 0 and measured.get('passed') is True,
        'role': role, 'context_id': session, 'actor_id': actor_id, 'host_positive_controls': positives,
        'binary_sha256': BINARY_SHA256, 'actual_probe': measured, 'coursework_complete': False,
        'scope': 'Read-only operator diagnostic in one actual gVisor actor per role; not an agent shell tool, host-compromise test or production multitenancy claim.'}
    keep('verification.json', (json.dumps(report, indent=2) + '\n').encode())
    print(json.dumps(report, indent=2)); return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--request-file', type=Path, required=True)
    parser.add_argument('--role', choices=['retrieval', 'drift'], required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); raise SystemExit(run(args.runtime_root, args.request_file, args.role, args.binary, args.output))
