"""Correct only the rejected owned MCP ingress to preserve standard bearer authorization, retaining its TLS and network objects."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse, json, socket, ssl, subprocess, sys, time
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from infra.gateway.mcp import HOST, resources


def run(args):
    """Test the original UID/spec/annotations before one narrow correction and require the actual accepted TLS identity."""
    out = args.output; out.mkdir(parents=True, exist_ok=False); k = ['/usr/local/bin/k3s', 'kubectl']

    def get(*values):
        """Read native non-secret Kubernetes objects for this fixed private route."""
        return json.loads(subprocess.check_output(k+list(values)+['-o', 'json'], stderr=subprocess.PIPE, timeout=30))

    def save(name, value):
        """Keep original safe objects and the exact submitted JSON patch."""
        (out/name).write_text(json.dumps(value, indent=2)+'\n')

    expected = next(v for v in resources() if v['kind'] == 'Ingress')
    original = next(v for v in json.loads((args.deployment/'manifests.json').read_bytes())['items'] if v['kind'] == 'Ingress')
    before = get('get', 'ingress', 'iot-mcp-retrieval', '-n', 'iot-agents')
    assert before['spec'] == original['spec'] == expected['spec'] and before['metadata']['annotations'] == original['metadata']['annotations']
    assert before['metadata']['annotations']['nginx.org/proxy-set-headers'] == 'Authorization: $http_x_mcp_authorization,X-MCP-Authorization:'
    save('before-ingress.json', before); save('before-events.json', get('get', 'events', '-n', 'iot-agents'))
    policy = get('get', 'networkpolicy', 'iot-mcp-gateway', '-n', 'iot-agents'); save('before-policy.json', policy)
    deployment = get('get', 'deployment', 'iot-gateway', '-n', 'iot-gateway'); save('before-gateway.json', deployment)
    patch = [{'op': 'test', 'path': '/metadata/uid', 'value': before['metadata']['uid']},
             {'op': 'test', 'path': '/spec', 'value': before['spec']},
             {'op': 'test', 'path': '/metadata/annotations', 'value': before['metadata']['annotations']},
             {'op': 'replace', 'path': '/metadata/annotations', 'value': expected['metadata']['annotations']}]
    save('patch.json', patch)
    result = subprocess.run(k+['patch', 'ingress', 'iot-mcp-retrieval', '-n', 'iot-agents', '--type=json', '--patch-file', str(out/'patch.json')], capture_output=True, timeout=30)
    (out/'patch.txt').write_bytes(result.stdout+result.stderr); assert result.returncode == 0
    gateway = get('get', 'service', 'iot-gateway', '-n', 'iot-gateway'); assert gateway['spec']['type'] == 'ClusterIP' and not gateway['spec'].get('externalIPs')
    context = ssl.create_default_context(cafile=str(args.deployment/'ca.crt'))
    leaf = sha256(ssl.PEM_cert_to_DER_cert((args.deployment/'server.crt').read_text())).hexdigest(); deadline = time.monotonic()+90
    while True:
        try:
            with context.wrap_socket(socket.create_connection((gateway['spec']['clusterIP'], 443), timeout=5), server_hostname=HOST) as peer:
                assert sha256(peer.getpeercert(binary_form=True)).hexdigest() == leaf
                tls = {'ca_verified': True, 'hostname_verified': True, 'leaf_sha256': leaf, 'tls_version': peer.version()}
            break
        except (OSError, AssertionError):
            if time.monotonic() >= deadline: raise RuntimeError('Corrected ingress still lacks its expected TLS identity') from None
            time.sleep(.5)
    after = get('get', 'ingress', 'iot-mcp-retrieval', '-n', 'iot-agents'); save('after-ingress.json', after)
    assert after['metadata']['uid'] == before['metadata']['uid'] and after['spec'] == before['spec']
    assert after['metadata']['annotations'] == expected['metadata']['annotations']
    current = get('get', 'networkpolicy', 'iot-mcp-gateway', '-n', 'iot-agents'); save('after-policy.json', current)
    assert (current['metadata']['uid'], current['spec']) == (policy['metadata']['uid'], policy['spec'])
    current = get('get', 'deployment', 'iot-gateway', '-n', 'iot-gateway'); save('after-gateway.json', current)
    assert current['metadata']['uid'] == deployment['metadata']['uid'] and current['spec'] == deployment['spec']
    save('after-events.json', get('get', 'events', '-n', 'iot-agents'))
    config = subprocess.check_output(k+['exec', 'deployment/iot-gateway', '-n', 'iot-gateway', '--', 'nginx', '-T'], stderr=subprocess.STDOUT, timeout=30)
    (out/'nginx-config.txt').write_bytes(config)
    report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'host': HOST, 'gateway_ip': gateway['spec']['clusterIP'],
              'tls': tls, 'ingress_uid': after['metadata']['uid'], 'existing_controller_and_network_preserved': True,
              'source_sha256': {n: sha256((ROOT/n).read_bytes()).hexdigest() for n in ['scripts/update-mcp-gateway-route.py', 'infra/gateway/mcp.py']},
              'scope': 'Only the rejected new ingress header/auth annotations corrected. Native scoped Bearer authentication is passed unchanged; full REST/MCP probes follow separately.'}
    save('verification.json', report); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['deployment', 'output']: parser.add_argument('--'+name, type=Path, required=True)
    run(parser.parse_args())
