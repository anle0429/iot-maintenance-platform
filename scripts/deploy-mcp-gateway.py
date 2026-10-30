"""Add a CA-verified private HTTPS retrieval route while preserving the existing controller, APIs and agent routes."""
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from pathlib import Path
import argparse, base64, json, os, socket, ssl, subprocess, sys, time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from infra.gateway.mcp import HOST, SERVICE, NAMESPACE, resources


def run(args):
    """Create only three new named resources and compare native identities/specifications before and after."""
    os.umask(0o077)
    root = args.runtime_root.resolve(); out = args.output.resolve(); private = args.private.resolve()
    if not root.is_relative_to('/srv/iot-data') or not out.is_relative_to(root/'outputs') or not private.is_relative_to(root/'data/runtime'):
        raise ValueError('Use the retained project runtime directories')
    session = json.loads((root/'data/runtime/ci/session.json').read_bytes())
    if datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc) < timedelta(minutes=30):
        raise ValueError('Require thirty actual CPU session minutes')
    out.mkdir(parents=True, exist_ok=False); private.mkdir(parents=True, exist_ok=False)
    k = ['/usr/local/bin/k3s', 'kubectl']

    def command(values, payload=None, name=None):
        """Suppress credential-bearing command input/output and retain only explicitly safe process diagnostics."""
        result = subprocess.run(values, input=payload, capture_output=True, timeout=60)
        if name: (out/name).write_bytes(result.stdout+result.stderr)
        if result.returncode: raise RuntimeError('Native command failed; inspect its retained operator phase')
        return result.stdout

    def get(*values):
        """Read current Kubernetes objects without publishing Secret bodies."""
        return json.loads(command(k+list(values)+['-o', 'json']))

    def save(name, value):
        """Preserve original non-secret objects or safe explicitly selected Secret identities."""
        (out/name).write_text(json.dumps(value, indent=2)+'\n')

    def snapshot():
        """Record existing route, API, agent and network identities to detect an accidental unrelated change."""
        docs = {'gateway': get('get', 'deployment', 'iot-gateway', '-n', 'iot-gateway'),
                'gateway-service': get('get', 'service', 'iot-gateway', '-n', 'iot-gateway')}
        for kind in ['deployments', 'services', 'networkpolicies', 'roles', 'rolebindings', 'sandboxagents', 'workerpools', 'modelconfigs']:
            docs[kind] = get('get', kind, '-n', NAMESPACE)
        docs['ingresses'] = get('get', 'ingresses', '--all-namespaces')
        for name in ['agent-gateway-auth', 'agent-gateway-tls', 'iot-retrieval-mcp-auth', 'iot-drift-mcp-auth']:
            value = get('get', 'secret', name, '-n', NAMESPACE)
            docs['secret-'+name] = {'uid': value['metadata']['uid'], 'type': value['type'],
                                        'data_sha256': {key: sha256(base64.b64decode(raw)).hexdigest() for key, raw in value['data'].items()}}
        return docs

    before = snapshot()
    for key, value in before.items(): save('before-'+key+'.json', value)
    deployment = before['gateway']; expected_image = json.loads((ROOT/'infra/gateway/assets.json').read_bytes())['image']
    assert deployment['spec']['template']['spec']['containers'][0]['image'] == expected_image
    assert '-watch-namespace=iot-gateway,iot-features,iot-observability,iot-agents' in deployment['spec']['template']['spec']['containers'][0]['args']
    gateway = before['gateway-service']; assert gateway['spec']['type'] == 'ClusterIP' and not gateway['spec'].get('externalIPs')
    backend = next(v for v in before['services']['items'] if v['metadata']['name'] == SERVICE)
    assert backend['spec']['type'] == 'ClusterIP' and not backend['spec'].get('externalIPs')
    assert next(v for v in before['deployments']['items'] if v['metadata']['name'] == SERVICE)['status']['availableReplicas'] == 2
    for kind, name in [('secret', 'mcp-gateway-tls'), ('ingress', 'iot-mcp-retrieval'), ('networkpolicy', 'iot-mcp-gateway')]:
        if command(k+['get', kind, name, '-n', NAMESPACE, '--ignore-not-found', '-o', 'name']).strip():
            raise ValueError('Preserve an existing owned resource; inspect the preceding release')
    command(['openssl', 'x509', '-in', str(args.ca), '-checkend', '3600', '-noout'], name='ca-validity.txt')
    (out/'ca.crt').write_bytes(args.ca.read_bytes())
    command(['openssl', 'req', '-new', '-newkey', 'rsa:3072', '-nodes', '-keyout', str(private/'server.key'),
             '-out', str(private/'server.csr'), '-subj', '/CN='+HOST], name='tls-request.txt')
    (private/'extensions.cnf').write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:'+HOST+'\n')
    command(['openssl', 'x509', '-req', '-in', str(private/'server.csr'), '-CA', str(args.ca), '-CAkey', str(args.ca_key),
             '-set_serial', '0x'+os.urandom(16).hex(), '-out', str(out/'server.crt'), '-days', '2', '-extfile', str(private/'extensions.cnf')], name='tls-sign.txt')
    command(['openssl', 'verify', '-CAfile', str(args.ca), str(out/'server.crt')], name='tls-verify.txt')
    secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'mcp-gateway-tls', 'namespace': NAMESPACE},
              'type': 'kubernetes.io/tls', 'stringData': {'tls.crt': (out/'server.crt').read_text(), 'tls.key': (private/'server.key').read_text()}}
    command(k+['create', '-f', '-'], payload=json.dumps(secret).encode())
    observed = get('get', 'secret', 'mcp-gateway-tls', '-n', NAMESPACE)
    save('tls-secret-identity.json', {'uid': observed['metadata']['uid'], 'type': observed['type'], 'keys': sorted(observed['data'])})
    docs = {'apiVersion': 'v1', 'kind': 'List', 'items': resources()}; save('manifests.json', docs)
    command(k+['create', '--dry-run=server', '-f', str(out/'manifests.json')], name='server-dry-run.txt')
    command(k+['create', '-f', str(out/'manifests.json')], name='create.txt')
    address = gateway['spec']['clusterIP']; context = ssl.create_default_context(cafile=str(args.ca))
    leaf = sha256(ssl.PEM_cert_to_DER_cert((out/'server.crt').read_text())).hexdigest(); deadline = time.monotonic()+90
    while True:
        try:
            with context.wrap_socket(socket.create_connection((address, 443), timeout=5), server_hostname=HOST) as peer:
                assert sha256(peer.getpeercert(binary_form=True)).hexdigest() == leaf
                tls = {'ca_verified': True, 'hostname_verified': True, 'leaf_sha256': leaf, 'tls_version': peer.version()}
            break
        except (OSError, AssertionError):
            if time.monotonic() >= deadline: raise RuntimeError('New route does not serve its expected TLS identity') from None
            time.sleep(.5)
    after = snapshot()
    for key, value in after.items(): save('after-'+key+'.json', value)
    for key, original in before.items():
        current = after[key]
        if key.startswith('secret-'): assert current == original
        elif 'items' in original:
            indexed = {v['metadata']['uid']: v for v in current['items']}
            for old in original['items']:
                new = indexed[old['metadata']['uid']]
                assert new.get('spec') == old.get('spec')
                assert new.get('rules') == old.get('rules') and new.get('roleRef') == old.get('roleRef') and new.get('subjects') == old.get('subjects')
                assert new['metadata'].get('annotations') == old['metadata'].get('annotations')
        else: assert current['metadata']['uid'] == original['metadata']['uid'] and current['spec'] == original['spec']
    save('new-ingress.json', get('get', 'ingress', 'iot-mcp-retrieval', '-n', NAMESPACE))
    save('new-networkpolicy.json', get('get', 'networkpolicy', 'iot-mcp-gateway', '-n', NAMESPACE))
    command(k+['exec', 'deployment/iot-gateway', '-n', 'iot-gateway', '--', 'nginx', '-T'], name='nginx-config.txt')
    report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'host': HOST, 'gateway_ip': address,
              'gateway_image': expected_image, 'tls': tls, 'existing_specs_and_credentials_preserved': True,
              'source_sha256': {n: sha256((ROOT/n).read_bytes()).hexdigest() for n in ['scripts/deploy-mcp-gateway.py', 'infra/gateway/mcp.py', 'infra/gateway/assets.json']},
              'scope': 'New private ingress, TLS Secret and narrow NetworkPolicy only. Actual authentication, REST and MCP requests are checked separately.'}
    save('verification.json', report)
    for path in out.iterdir():
        if path.is_file(): assert (private/'server.key').read_bytes() not in path.read_bytes()
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root', 'output', 'private', 'ca', 'ca-key']: parser.add_argument('--'+name, type=Path, required=True)
    run(parser.parse_args())
