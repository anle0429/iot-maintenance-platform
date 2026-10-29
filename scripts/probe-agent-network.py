"""Verify worker-level MCP isolation with actual gVisor actors, packet counters and a narrow allow/deny control."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
import argparse
import base64
import json
import os
import re
import subprocess
import time


def actor_id(role, context):
    """Match kagent's native name-length rule before correlating the completed A2A session."""
    candidate = 'asr-iot-agents-iot-' + role + '-' + context.strip().lower().replace('_', '-')
    return candidate if len(candidate) <= 63 else 'asr-' + sha256(('iot-agents/iot-' + role + '/' + context).encode()).hexdigest()[:24]


def reject_counters(raw, pod_ip):
    """Read only the observed worker's Kube-router deny counters, rejecting an unrecognized ruleset."""
    lines = raw.decode().splitlines()
    chains = {line.rsplit(' -j ', 1)[1].split()[0] for line in lines
              if ' -s ' + pod_ip + '/32 ' in line and ' -j KUBE-POD-FW-' in line}
    if not chains: raise ValueError('No source-specific worker firewall chain observed')
    selected = []
    for line in lines:
        match = re.match(r'^\[(\d+):(\d+)\] -A (\S+) ', line)
        if match and match[3] in chains and (' -j REJECT' in line or ' -j DROP' in line):
            selected.append({'chain': match[3], 'packets': int(match[1]), 'bytes': int(match[2]), 'rule': line.split('] ', 1)[1]})
    if not selected: raise ValueError('The worker has no observable deny rule')
    return {'packets': sum(r['packets'] for r in selected), 'rules': selected}


def phase_accepted(name, diagnostic, delta):
    """A timeout alone never proves denial; require a counted rejection and the reversible positive control."""
    if diagnostic['allowed_mcp']['http_status'] != 200 or not diagnostic['filesystem_boundary_verified']:
        return False
    blocked = diagnostic['blocked_other_role_mcp']
    if name == 'allow-control': return blocked['http_status'] == 200 and delta == 0
    return blocked['http_status'] == 0 and bool(blocked.get('error_type')) and delta > 0


def run(root, output, prior_chat):
    """Resume only two owned completed actors and always restore their suspended state and temporary policy."""
    root, output, prior_chat = root.resolve(), output.resolve(), prior_chat.resolve()
    if (os.geteuid() != 0 or not output.is_relative_to(root / 'outputs')
            or not prior_chat.is_relative_to(root / 'outputs') or prior_chat.name != 'agent-chat-29m'):
        raise ValueError('Require sudo, a fresh project output and the source-recorded completed conversation')
    output.mkdir(exist_ok=False); os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'
    k = ['/usr/local/bin/k3s', 'kubectl']; private = []
    for name in ['agent-platform.json', 'mcp-tools.json']:
        for value in json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values():
            private.extend([value.encode(), base64.b64encode(value.encode())])

    def keep(name, body):
        """Preserve exact native bytes only after checking actual short-lived and stored credential values."""
        if any(secret in body for secret in private): raise ValueError('Refuse credential-bearing receipt')
        path = output / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)

    def command(name, args, payload=None, env=None, allow_failure=False):
        """Retain bounded native command streams without logging credential-bearing arguments or environments."""
        result = subprocess.run(args, input=payload, env=env, capture_output=True, timeout=60)
        for suffix, raw in [('stdout', result.stdout), ('stderr', result.stderr), ('exit', str(result.returncode).encode() + b'\n')]: keep(name + '.' + suffix, raw)
        if result.returncode and not allow_failure: raise RuntimeError(name + ' failed; original receipt retained')
        return result

    policies = json.loads(command('policies-before', k + ['get', 'networkpolicy', '-n', 'iot-agents', '-l', 'iot.coursework/agent-network=true', '-o', 'json']).stdout)['items']
    if {p['metadata']['name'] for p in policies} != {'iot-' + r + '-worker-egress' for r in ['retrieval', 'drift', 'coordinator']}:
        raise ValueError('Install all three owned network policies first')
    deployment = json.loads(command('controller', k + ['get', 'deployment', 'kagent-controller', '-n', 'iot-agents', '-o', 'json']).stdout)
    token = subprocess.check_output(k + ['create', 'token', deployment['spec']['template']['spec']['serviceAccountName'], '-n', 'iot-agents', '--audience=api.ate-system.svc', '--duration=10m']).strip()
    private.extend([token, base64.b64encode(token)]); env = {**os.environ, 'IOT_ATE_AUTH': token.decode()}
    assets = root / 'outputs/agent-isolation-assets-29a'
    if sha256((assets / 'grpcurl').read_bytes()).hexdigest() != '32a5cc701c4d2c84980ed348c6383926a8b397d96a4cd9303769ee868cac2251':
        raise ValueError('Require the verified grpcurl 1.9.4 binary')
    api = json.loads(command('api-service', k + ['get', 'service', 'api', '-n', 'ate-system', '-o', 'json']).stdout)
    base = [str(assets / 'grpcurl'), '-cacert', str(assets / 'ateapi-ca.crt'), '-authority', 'api.ate-system.svc',
            '-expand-headers', '-H', 'authorization: Bearer ${IOT_ATE_AUTH}', '-max-time', '45', '-d', '@', api['spec']['clusterIP'] + ':443']
    all_roles = {}; opener = build_opener(ProxyHandler({}))
    for role in ['retrieval', 'drift']:
        request = prior_chat / (role + '-request.json'); raw = request.read_bytes(); keep(role + '/request.json', raw)
        context = json.loads(raw)['params']['message']['contextId']
        actor = actor_id(role, context)
        body = json.dumps({'actor': {'atespace': 'iot-agents', 'name': actor}}).encode()

        def grpc(label, method):
            """Authenticate the native lifecycle call for this one source-correlated existing actor."""
            result = command(role + '/' + label, base + ['ateapi.Control/' + method], body, env)
            return json.loads(result.stdout)

        before = grpc('actor-before', 'GetActor')
        if before['status'] != 'STATUS_SUSPENDED': raise ValueError('Preserve an actor that is not already suspended')
        resumed = False; temporary = False; temporary_name = 'iot-' + role + '-probe-positive-control'
        try:
            resumed = True; grpc('resume', 'ResumeActor')
            actual = grpc('actor-running', 'GetActor')
            if actual['status'] != 'STATUS_RUNNING': raise ValueError('Native actor did not reach Running')
            worker_name = actual['ateomPodName']
            worker = json.loads(command(role + '/worker', k + ['get', 'pod', worker_name, '-n', 'iot-agents', '-o', 'json']).stdout)
            if worker['metadata']['uid'] != actual['ateomPodUid'] or worker['metadata']['labels'].get('ate.dev/worker-pool') != 'iot-' + role + '-pool':
                raise ValueError('Actor-to-worker identity or role differs')
            phases = []
            for phase in ['deny-before', 'allow-control', 'deny-restored']:
                if phase == 'allow-control':
                    opposite = 'drift' if role == 'retrieval' else 'retrieval'
                    doc = {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': {'name': temporary_name, 'namespace': 'iot-agents'},
                        'spec': {'podSelector': {'matchLabels': {'ate.dev/worker-pool': 'iot-' + role + '-pool'}}, 'policyTypes': ['Egress'],
                        'egress': [{'to': [{'podSelector': {'matchLabels': {'app.kubernetes.io/instance': 'iot-' + opposite + '-mcp'}}}], 'ports': [{'protocol': 'TCP', 'port': 8080}]}]}}
                    command(role + '/control-create', k + ['create', '-f', '-'], json.dumps(doc).encode()); temporary = True
                    time.sleep(5)
                elif phase == 'deny-restored':
                    command(role + '/control-delete', k + ['delete', 'networkpolicy', temporary_name, '-n', 'iot-agents', '--wait=true']); temporary = False
                    time.sleep(5)
                start = reject_counters(command(role + '/' + phase + '/rules-before', ['iptables-save', '-c', '-t', 'filter']).stdout, worker['status']['podIP'])
                command(role + '/' + phase + '/operator', ['python3', '/tmp/iot-probe-agent-isolation-29a.py', '--runtime-root', str(root),
                    '--request-file', str(request), '--role', role, '--binary', '/tmp/iot-sandbox-probe-linux-amd64', '--output', str(output / role / phase / 'probe')], allow_failure=True)
                diagnostic = json.loads((output / role / phase / 'probe/diagnostic.stdout').read_bytes())
                end = reject_counters(command(role + '/' + phase + '/rules-after', ['iptables-save', '-c', '-t', 'filter']).stdout, worker['status']['podIP'])
                delta = end['packets'] - start['packets']
                record = {'phase': phase, 'passed': phase_accepted(phase, diagnostic, delta), 'reject_packet_delta': delta,
                          'counter_before': start, 'counter_after': end, 'diagnostic': diagnostic}
                keep(role + '/' + phase + '/acceptance.json', (json.dumps(record, indent=2) + '\n').encode()); phases.append(record)
                if not record['passed']: raise ValueError('Native ' + role + ' ' + phase + ' failed')
            all_roles[role] = {'passed': True, 'actor_id': actor, 'actor_uid': actual['metadata']['uid'], 'worker_uid': worker['metadata']['uid'], 'worker_ip': worker['status']['podIP'], 'phases': phases}
        finally:
            try:
                if temporary: command(role + '/cleanup-control', k + ['delete', 'networkpolicy', temporary_name, '-n', 'iot-agents', '--wait=true'])
            finally:
                if resumed:
                    grpc('suspend', 'SuspendActor'); after = grpc('actor-after', 'GetActor')
                    if after['status'] != 'STATUS_SUSPENDED': raise ValueError('Require restoration of the original suspended lifecycle')
    command('policies-after', k + ['get', 'networkpolicy', '-n', 'iot-agents', '-o', 'json'])
    report = {'passed': len(all_roles) == 2, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'roles': all_roles,
              'coursework_complete': False, 'scope': 'Direct specialist MCP egress inside two actual existing gVisor actors. Trusted platform/controller routes remain allowed. Operator diagnostics do not establish nonroot execution, host-compromise resistance or production multitenancy.'}
    keep('verification.json', (json.dumps(report, indent=2) + '\n').encode()); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prior-chat', type=Path, required=True)
    args = parser.parse_args(); run(args.runtime_root, args.output, args.prior_chat)
