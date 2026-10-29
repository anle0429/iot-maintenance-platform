"""Install role-specific worker egress policies without replacing the existing agent or MCP releases."""
from datetime import datetime, timezone
from hashlib import sha256
from ipaddress import ip_network
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
import argparse
import base64
import json
import os
import re
import subprocess
import yaml

ROLES = {'retrieval', 'drift', 'coordinator'}
SOURCE_FILES = {
    'scripts/configure-agent-network.py', 'infra/charts/iot-agent-network/Chart.yaml',
    'infra/charts/iot-agent-network/values.yaml', 'infra/charts/iot-agent-network/templates/network.yaml',
}


def private_control_cidr(value):
    """Accept only one private IPv4 control-node address, never an unrestricted network."""
    network = ip_network(value, strict=True)
    if network.version != 4 or network.prefixlen != 32 or not network.network_address.is_private:
        raise ValueError('Require the observed private control-node /32')
    return str(network)


def validate_policies(documents, control_cidr):
    """Reject broad peers, missing destinations and changes to ingress before applying the release."""
    documents = [d for d in documents if d is not None]
    expected = {'iot-' + role + '-worker-egress' for role in ROLES}
    if len(documents) != 3 or {d['metadata']['name'] for d in documents} != expected:
        raise ValueError('Require exactly the three dedicated worker policies')
    for doc in documents:
        name = doc['metadata']['name']; role = name.removeprefix('iot-').removesuffix('-worker-egress')
        spec = doc['spec']
        if (doc.get('kind') != 'NetworkPolicy' or spec.get('policyTypes') != ['Egress']
                or 'ingress' in spec or spec['podSelector'] != {'matchLabels': {'ate.dev/worker-pool': 'iot-' + role + '-pool'}}):
            raise ValueError('Require role-specific worker egress selection')
        peers = [peer for rule in spec['egress'] for peer in rule.get('to', [])]
        if any(not peer or not (peer.get('namespaceSelector') or peer.get('ipBlock')) for peer in peers):
            raise ValueError('Reject unrestricted destinations')
        if [p['ipBlock'] for p in peers if 'ipBlock' in p] != [{'cidr': private_control_cidr(control_cidr)}]:
            raise ValueError('Preserve the single private API-server destination')
        instances = {p.get('podSelector', {}).get('matchLabels', {}).get('app.kubernetes.io/instance') for p in peers}
        wanted = set() if role == 'coordinator' else {'iot-' + role + '-mcp'}
        if {v for v in instances if v is not None} != wanted:
            raise ValueError('Allow only the specialist own MCP; coordinator delegates through the controller')
        namespaces = {p.get('namespaceSelector', {}).get('matchLabels', {}).get('kubernetes.io/metadata.name') for p in peers}
        if namespaces - {None, 'ate-system', 'kube-system', 'iot-llm', 'iot-agents'}:
            raise ValueError('Reject destinations outside the owned private platform')
        def peer(namespace, labels=None):
            """Build one explicit namespace-and-pod destination for exact policy comparison."""
            value = {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': namespace}}}
            if labels is not None: value['podSelector'] = {'matchLabels': labels}
            return value
        expected_rules = [
            {'to': [peer('ate-system')]},
            {'to': [peer('kube-system', {'k8s-app': 'kube-dns'})], 'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]},
            {'to': [{'ipBlock': {'cidr': control_cidr}}], 'ports': [{'protocol': 'TCP', 'port': 6443}]},
            {'to': [peer('iot-llm', {'llm-d-router-gateway': 'iot-router-epp'})], 'ports': [{'protocol': 'TCP', 'port': 8081}]},
            {'to': [peer('iot-agents', {'app.kubernetes.io/name': 'kagent', 'app.kubernetes.io/component': 'controller'})], 'ports': [{'protocol': 'TCP', 'port': 8083}]},
        ]
        if role != 'coordinator':
            expected_rules.append({'to': [peer('iot-agents', {'app.kubernetes.io/instance': 'iot-' + role + '-mcp'})], 'ports': [{'protocol': 'TCP', 'port': 8080}]})
        if spec['egress'] != expected_rules:
            raise ValueError('Destinations, selectors and ports must match the reviewed private allow rules exactly')
    return True


def run(root, source, run_name):
    """Guard a fresh Helm install, preserve native receipts and remove only that release if post-checks fail."""
    root, source = root.resolve(), source.resolve()
    if os.geteuid() != 0 or not re.fullmatch(r'agent-network-[a-z0-9-]+', run_name):
        raise ValueError('Use sudo and a fresh owned agent-network run')
    if not source.is_relative_to(root / 'data/llm-source'):
        raise ValueError('Require project-local sealed source')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    if set(seal['files']) != SOURCE_FILES:
        raise ValueError('Seal must cover exactly this operator and all chart inputs')
    for name, digest in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Sealed source differs: ' + name)
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    if (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds() < 1800:
        raise ValueError('Require thirty minutes in the existing approved session')
    output = root / 'outputs' / run_name; output.mkdir(exist_ok=False)
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'
    helm = str(root / 'data/runtime/kubernetes-assets-27/helm/helm'); k = ['/usr/local/bin/k3s', 'kubectl']
    private = []
    for name in ['agent-platform.json', 'mcp-tools.json']:
        for value in json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values():
            private.extend([value.encode(), base64.b64encode(value.encode())])

    def command(label, args, payload=None):
        """Keep original bounded command streams only after scanning actual credential values."""
        result = subprocess.run(args, input=payload, capture_output=True, timeout=360)
        if any(secret in result.stdout or secret in result.stderr for secret in private):
            raise ValueError('Credential-bearing receipt refused')
        for suffix, raw in [('stdout', result.stdout), ('stderr', result.stderr), ('exit', str(result.returncode).encode() + b'\n')]:
            (output / (label + '.' + suffix)).write_bytes(raw)
        if result.returncode: raise RuntimeError(label + ' failed; preserve original receipts')
        return result.stdout

    (output / 'source.json').write_text(json.dumps(seal, indent=2) + '\n')
    installed = False
    try:
        releases = json.loads(command('releases-before', [helm, 'list', '-n', 'iot-agents', '-a', '-o', 'json']))
        if any(r['name'] == 'iot-agent-network' for r in releases):
            raise ValueError('Preserve the existing network release; this operator only installs once')
        nodes = json.loads(command('nodes-before', k + ['get', 'nodes', '-o', 'json']))['items']
        controls = [n for n in nodes if n['metadata']['name'] == 'iot-cw-vm' and 'node-role.kubernetes.io/control-plane' in n['metadata']['labels']]
        if len(controls) != 1: raise ValueError('Require the retained control node')
        addresses = [a['address'] for a in controls[0]['status']['addresses'] if a['type'] == 'InternalIP']
        if len(addresses) != 1: raise ValueError('Require one observed private control address')
        cidr = private_control_cidr(addresses[0] + '/32')
        service = json.loads(command('controller-service', k + ['get', 'service', 'kagent-controller', '-n', 'iot-agents', '-o', 'json']))
        with build_opener(ProxyHandler({})).open('http://' + service['spec']['clusterIP'] + ':8083/api/substrate/status', timeout=20) as response:
            inventory = json.load(response)
        if any(a['status'] not in {'Suspended', 'Paused'} for a in inventory['data']['actors']):
            raise ValueError('Wait for all existing conversations to suspend before policy installation')
        (output / 'actors-before.json').write_text(json.dumps(inventory, indent=2) + '\n')
        chart = str(source / 'infra/charts/iot-agent-network')
        rendered = command('rendered', [helm, 'template', 'iot-agent-network', chart, '-n', 'iot-agents', '--set-string', 'controlCIDR=' + cidr])
        validate_policies(list(yaml.safe_load_all(rendered)), cidr)
        command('server-validation', k + ['apply', '--dry-run=server', '-n', 'iot-agents', '-f', '-'], rendered)
        command('install', [helm, 'install', 'iot-agent-network', chart, '-n', 'iot-agents', '--set-string', 'controlCIDR=' + cidr, '--atomic', '--wait', '--timeout', '4m'])
        installed = True
        actual = json.loads(command('policies-after', k + ['get', 'networkpolicy', '-n', 'iot-agents', '-l', 'iot.coursework/agent-network=true', '-o', 'json']))['items']
        validate_policies(actual, cidr)
        command('workers-after', k + ['get', 'workerpools', '-n', 'iot-agents', '-o', 'json'])
        command('packet-rules-after', ['iptables-save', '-c', '-t', 'filter'])
        report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'control_cidr': cidr,
            'policies': sorted(d['metadata']['name'] for d in actual), 'actor_egress_acceptance_verified': False,
            'coursework_complete': False, 'scope': 'Dedicated worker egress policy installed; actual actor allow/deny and normal conversations still require measurement.'}
    except Exception as error:
        if installed: command('rollback-uninstall', [helm, 'uninstall', 'iot-agent-network', '-n', 'iot-agents', '--wait', '--timeout', '3m'])
        (output / 'verification.json').write_text(json.dumps({'passed': False, 'error_type': type(error).__name__, 'coursework_complete': False}, indent=2) + '\n')
        raise
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True); parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--run', required=True)
    args = parser.parse_args(); run(args.runtime_root, args.source_root, args.run)
