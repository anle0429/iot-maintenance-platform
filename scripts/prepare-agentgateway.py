"""Render the private agentgateway candidate and guarded client changes without applying them."""

from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from infra.agentgateway.candidate import BACKENDS, DNS_NAME, NAMESPACE, PORTS, ROLES, manifests
from infra.agentgateway.delegation import plan as delegation_plan


def patch_document(resource, path, expected, replacement):
    """Require the exact observed resource version and value before a reviewed JSON Patch."""
    pointer = '/' + '/'.join(path)
    actual = resource
    for key in path:
        actual = actual[key]
    if actual != expected:
        raise ValueError('The retained configuration differs; inspect the actual resource before promotion')
    return [{'op': 'test', 'path': '/metadata/resourceVersion', 'value': resource['metadata']['resourceVersion']},
            {'op': 'test', 'path': pointer, 'value': actual},
            {'op': 'replace', 'path': pointer, 'value': replacement}]


def resource(path, kind, name):
    """Accept only a named existing resource readback in the retained namespace."""
    value = json.loads(path.read_bytes())
    if (value['kind'] != kind or value['metadata']['name'] != name
            or value['metadata']['namespace'] != NAMESPACE or not value['metadata']['uid']):
        raise ValueError('Unexpected existing resource identity')
    return value


def client_patches(source, rollback):
    """Keep existing identities, timeouts, credentials, tool names and delegation while changing only client routes."""
    specs = [('model', 'ModelConfig', 'default-model-config', ('spec', 'openAI', 'baseUrl'),
              'http://' + BACKENDS['model'] + '/v1', f'http://{DNS_NAME}:{PORTS["model"]}/v1')]
    specs += [(role, 'RemoteMCPServer', 'iot-' + role + '-tools', ('spec', 'url'),
               'http://' + BACKENDS[role] + '/mcp', f'http://{DNS_NAME}:{PORTS[role]}/mcp') for role in ROLES[:2]]
    results = {}
    for role, kind, name, field, previous, new in specs:
        obj = resource(source / (role + '.json'), kind, name)
        results[role] = patch_document(obj, field, new if rollback else previous, previous if rollback else new)
    for role in ROLES:
        obj = resource(source / ('agent-' + role + '.json'), 'SandboxAgent', 'iot-' + role)
        allowed = obj['spec']['sandbox']['network']['allowedDomains']
        if not isinstance(allowed, list) or not allowed or len(allowed) != len(set(allowed)):
            raise ValueError('Expected a concrete existing SandboxAgent domain allowlist')
        if (DNS_NAME in allowed) != rollback:
            raise ValueError('Gateway hostname does not match the requested patch direction')
        new = [host for host in allowed if host != DNS_NAME] if rollback else [*allowed, DNS_NAME]
        results['agent-' + role] = patch_document(obj, ('spec', 'sandbox', 'network', 'allowedDomains'), allowed, new)
    return results


def main():
    """Write reviewable manifests and optional guarded patches to a new local directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-hashes', type=Path, required=True, help='JSON map containing only scoped SHA-256 hashes')
    parser.add_argument('--run', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--client-readbacks', type=Path)
    parser.add_argument('--rollback-clients', action='store_true')
    parser.add_argument('--delegation-key-hash', help='Distinct SHA-256 client hash for specialist A2A only; never a plaintext bearer')
    parser.add_argument('--delegation-readbacks', type=Path, help='Fresh owned gateway, controller and namespace-policy JSON readbacks for a guarded plan')
    args = parser.parse_args()
    if args.rollback_clients and args.client_readbacks is None:
        parser.error('--rollback-clients requires fresh readbacks from the candidate-connected resources')
    if args.delegation_readbacks and not args.delegation_key_hash:
        parser.error('--delegation-readbacks requires a distinct --delegation-key-hash')
    if args.delegation_key_hash and args.client_readbacks:
        parser.error('Do not mix the complete delegation rollout with legacy temporary client patches')
    hashes = json.loads(args.key_hashes.read_bytes())
    items = manifests(hashes, args.run, delegation_hash=args.delegation_key_hash)
    patches = client_patches(args.client_readbacks, args.rollback_clients) if args.client_readbacks else {}
    delegated = None
    if args.delegation_readbacks:
        source = args.delegation_readbacks
        names = {'ConfigMap':'gateway-configmap.json', 'Deployment':'gateway-deployment.json',
                 'NetworkPolicy':'gateway-networkpolicy.json', 'Service':'gateway-service.json'}
        observed = [resource(source / path, kind, 'iot-agentgateway-candidate') for kind,path in names.items()]
        controller = resource(source / 'controller-deployment.json', 'Deployment', 'kagent-controller')
        policies = json.loads((source / 'namespace-networkpolicies.json').read_bytes())
        if policies.get('kind') != 'NetworkPolicyList':
            raise ValueError('Require the original namespace NetworkPolicyList readback')
        delegated = delegation_plan(observed, policies['items'],
            controller['spec']['template']['metadata']['labels'], hashes, args.delegation_key_hash, args.run)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'candidate.json').write_text(json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': items}, indent=2)+'\n')
    if delegated is not None:
        (args.output / 'delegation-plan.json').write_text(json.dumps(delegated, indent=2)+'\n')
    for name, patch in patches.items():
        (args.output / (name + '-patch.json')).write_text(json.dumps(patch, indent=2)+'\n')


if __name__ == '__main__':
    main()
