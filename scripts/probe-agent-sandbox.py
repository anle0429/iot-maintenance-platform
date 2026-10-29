"""Run read-only operator diagnostics inside an existing gVisor actor without adding an execution tool to the agent."""
from hashlib import sha256
from pathlib import Path
from urllib.request import build_opener, ProxyHandler
import argparse
import json
import re
import subprocess


def run(root, actor_id, output, request_file=None, role='retrieval', operator_user='0:0'):
    """Observe actor identity, host isolation and allowed/denied HTTP destinations using the pinned native runsc."""
    root = root.resolve(); output = output.resolve()
    if request_file:
        request_file = request_file.resolve()
        if not request_file.is_relative_to(root / 'outputs') or request_file.name != role + '-request.json':
            raise ValueError('Derive only the selected role actor from a retained native request')
        session = json.loads(request_file.read_bytes())['params']['message']['contextId']
        candidate = 'asr-iot-agents-iot-' + role + '-' + session.strip().lower().replace('_', '-')
        actor_id = candidate if len(candidate) <= 63 else 'asr-' + sha256(('iot-agents/iot-' + role + '/' + session).encode()).hexdigest()[:24]
    if not re.fullmatch(r'asr-[a-z0-9-]+', actor_id) or not output.is_relative_to(root / 'outputs'):
        raise ValueError('Use one known project actor and a new output directory')
    output.mkdir(exist_ok=False)
    binary = Path('/var/lib/ateom-gvisor/static-files/runsc-f18a948bf9c8bbb54eb998549a3a8d719a1c7de2efbe8fdd2ff0ee5fecd06f19')
    if sha256(binary.read_bytes()).hexdigest() != binary.name.removeprefix('runsc-'):
        raise ValueError('The actual gVisor binary differs from its pinned checksum')
    state = Path('/var/lib/ateom-gvisor/actors') / ('iot-agents:' + actor_id) / 'runsc-state'
    if not state.is_dir():
        raise ValueError('The actor is not running under the owned namespace')
    k = ['/usr/local/bin/k3s', 'kubectl']
    service = json.loads(subprocess.check_output(k + ['get', 'service', 'kagent-controller', '-n', 'iot-agents', '-o', 'json']))
    with build_opener(ProxyHandler({})).open('http://' + service['spec']['clusterIP'] + ':8083/api/substrate/status', timeout=15) as response:
        inventory = json.load(response)['data']
    actors = [a for a in inventory['actors'] if a['actorId'] == actor_id and a['atespace'] == 'iot-agents' and a['status'] == 'Running']
    if len(actors) != 1 or actors[0]['ateomPodNamespace'] != 'iot-agents':
        raise ValueError('Require one actual running actor and its observed worker')
    worker = actors[0]['ateomPodName']
    (output / 'actor-readback.json').write_text(json.dumps(actors[0], indent=2) + '\n')
    # runsc's process IDs and state belong to the trusted worker's PID namespace.
    prefix = k + ['exec', '-n', 'iot-agents', worker, '--', str(binary), '--root=' + str(state),
                  'exec', '--user=' + operator_user, 'kagent']
    checks = [
        ('identity', ['/usr/bin/id']),
        ('kernel', ['/usr/bin/uname', '-a']),
        ('main-process', ['/usr/bin/cat', '/proc/1/status']),
        ('no-kubernetes-token', ['/bin/sh', '-c', 'test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token']),
        ('no-host-data', ['/bin/sh', '-c', 'test ! -e /srv/iot-data']),
        ('allowed-mcp', ['/usr/bin/curl', '--max-time', '8', '--silent', '--show-error', '--output', '/dev/null', '--write-out', '%{http_code}',
                         'http://iot-retrieval-mcp.iot-agents.svc.cluster.local:8080/health/live']),
        ('blocked-external', ['/usr/bin/curl', '--max-time', '8', '--silent', '--show-error', '--output', '/dev/null', '--write-out', '%{http_code}', 'http://example.com/']),
    ]
    statuses = {}
    for label, arguments in checks:
        result = subprocess.run(prefix + arguments, capture_output=True, timeout=20)
        (output / (label + '.stdout')).write_bytes(result.stdout)
        (output / (label + '.stderr')).write_bytes(result.stderr)
        (output / (label + '.exit')).write_text(str(result.returncode) + '\n')
        statuses[label] = {'exit': result.returncode, 'command': prefix + arguments}
    report = {'actor_id': actor_id, 'checks': statuses, 'operator_exec_user': operator_user,
        'runsc_sha256': sha256(binary.read_bytes()).hexdigest(),
        'scope': 'Read-only operator exec inside one existing actor. This adds no agent shell tool and makes no host compromise or multitenancy claim.'}
    report['filesystem_boundary_verified'] = (statuses['identity']['exit'] == 0
        and statuses['no-kubernetes-token']['exit'] == statuses['no-host-data']['exit'] == 0)
    report['egress_policy_verified'] = (
        (output / 'allowed-mcp.stdout').read_bytes() == b'200'
        and (output / 'blocked-external.stdout').read_bytes() == b'403')
    report['passed'] = report['filesystem_boundary_verified'] and report['egress_policy_verified']
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--actor-id')
    parser.add_argument('--request-file', type=Path)
    parser.add_argument('--role', choices=['retrieval', 'drift', 'coordinator'], default='retrieval')
    parser.add_argument('--operator-user', choices=['0:0', '1001:1001'], default='0:0')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); raise SystemExit(run(args.runtime_root, args.actor_id, args.output, args.request_file, args.role, args.operator_user))
