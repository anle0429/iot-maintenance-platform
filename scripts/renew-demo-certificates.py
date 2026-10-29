"""Renew the retained lab's certificates using its protected existing keys, with backups and verified TLS after reload."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler, ProxyHandler
import argparse
import base64
import http.client
import json
import os
import re
import socket
import ssl
import subprocess
import time


class NoRedirect(HTTPRedirectHandler):
    """Do not forward private unseal material to any redirected endpoint."""
    def redirect_request(self, *args):
        """Keep the explicit CA-verified Vault request on its original endpoint."""
        return None


def command(args, data=None):
    """Keep command output private; certificate metadata alone is returned in the public receipt."""
    return subprocess.check_output(args, input=data, stderr=subprocess.PIPE)


def certificate(path):
    """Extract public validity and fingerprints without reading or exporting private key material."""
    return {'sha256': sha256(path.read_bytes()).hexdigest(), 'metadata': command(['openssl', 'x509', '-in', str(path), '-noout', '-dates', '-subject', '-fingerprint', '-sha256']).decode().splitlines()}


def atomic_replace(path, raw):
    """Replace an operational certificate atomically while preserving its existing owner and mode."""
    stat = path.stat(); temporary = path.with_name(path.name + '.renewing')
    with temporary.open('xb') as stream: stream.write(raw)
    os.chown(temporary, stat.st_uid, stat.st_gid); temporary.chmod(stat.st_mode & 0o777); temporary.replace(path)


def run(args):
    """Issue new certificates, preserve old bytes, keep Raft/key/data identities and verify both TLS endpoints."""
    if os.name != 'posix' or os.geteuid() != 0: raise ValueError('Run with sudo inside the existing Linux VM')
    root = args.runtime_root.resolve(); output = args.output.resolve()
    if not root.is_relative_to('/srv/iot-data') or not output.is_relative_to(root / 'outputs') or not re.fullmatch(r'cert-[a-z0-9-]{1,40}', args.run): raise ValueError('Use a new cert-* run inside this lab project')
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    if (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds() < 1800: raise ValueError('Require30minutes in the approved VM session')
    work = root / 'data/runtime/demo-certificates' / args.run
    if work.exists() or output.exists(): raise ValueError('Preserve existing certificate receipts and choose a new run')
    os.umask(0o077); work.mkdir(parents=True, mode=0o700); output.mkdir(parents=True)
    gateway = root / 'outputs/gateway-28/deploy'; private = root / 'data/runtime/gateway-28-private'; vault = root / 'data/runtime/vault-server-28'
    authority = gateway / 'ca.crt'; ca_key = private / 'ca.key'; vault_ca = root / 'outputs/vault-28/resume-active/ca.crt'
    if command(['openssl', 'x509', '-in', str(authority), '-pubkey', '-noout']) != command(['openssl', 'pkey', '-in', str(ca_key), '-pubout']): raise ValueError('Existing CA key differs from authority')
    paths = [authority, vault_ca, gateway / 'server.crt', vault / 'server.crt']
    before = {str(path.relative_to(root)): certificate(path) for path in paths}
    backup = {path: path.read_bytes() for path in paths}
    for index, path in enumerate(paths): (work / f'original-{index}.crt').write_bytes(backup[path])
    key_hashes = {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest() for path in [ca_key, private / 'server.key', vault / 'server.key']}
    report = {'passed': False, 'source_sha256': sha256(Path(__file__).read_bytes()).hexdigest(), 'before': before,
              'existing_private_keys_reused': True, 'old_bytes_backed_up': True}
    k = ['/usr/local/bin/k3s', 'kubectl']; namespaces = ['iot-gateway', 'iot-features', 'iot-observability']; secrets = {}
    def vault_request(ca, path, body=None):
        """Use explicit certificate and hostname validation; unseal values never enter the receipt."""
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(cafile=str(ca))), NoRedirect())
        req = Request('https://10.52.0.1:18200/v1/' + path, data=json.dumps(body).encode() if body is not None else None, headers={'Content-Type': 'application/json'})
        try: response = opener.open(req, timeout=10)
        except HTTPError as error: response = error
        with response: return response.status, json.load(response)
    def activate(ca):
        """Wait for persistent Raft startup and unseal only an already initialized instance."""
        for attempt in range(40):
            try: code, health = vault_request(ca, 'sys/health'); break
            except OSError:
                if attempt == 39: raise
                time.sleep(1)
        if not health.get('initialized'): raise ValueError('Preserve unexpected uninitialized Vault; do not init')
        if health['sealed']:
            material = json.loads((root / 'data/runtime/vault-28-private/bootstrap.json').read_bytes())
            vault_request(ca, 'sys/unseal', {'key': material['keys_base64'][0]}); material = None
        for _ in range(40):
            code, health = vault_request(ca, 'sys/health')
            if code == 200 and not health['sealed'] and not health.get('standby'): return health
            time.sleep(1)
        raise ValueError('Vault did not become active')
    try:
        ca = work / 'ca.crt'
        command(['openssl', 'req', '-new', '-x509', '-key', str(ca_key), '-sha256', '-days', '14', '-subj', '/CN=IoT coursework private lab CA', '-out', str(ca),
                 '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign'])
        issued = {}
        for name, key, subject, sans in [('gateway', private / 'server.key', 'features.iot.test', 'DNS:metrics.iot.test,DNS:logs.iot.test,DNS:traces.iot.test,DNS:features.iot.test'),
                                        ('vault', vault / 'server.key', '10.52.0.1', 'IP:10.52.0.1')]:
            csr = work / (name + '.csr'); cert = work / (name + '.crt'); extensions = work / (name + '.ext')
            extensions.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=' + sans + '\n')
            command(['openssl', 'req', '-new', '-key', str(key), '-subj', '/CN=' + subject, '-out', str(csr)])
            command(['openssl', 'x509', '-req', '-in', str(csr), '-CA', str(ca), '-CAkey', str(ca_key), '-set_serial', '0x' + os.urandom(16).hex(), '-out', str(cert), '-days', '7', '-sha256', '-extfile', str(extensions)])
            command(['openssl', 'verify', '-CAfile', str(ca), str(cert)])
            if command(['openssl', 'x509', '-in', str(cert), '-pubkey', '-noout']) != command(['openssl', 'pkey', '-in', str(key), '-pubout']): raise ValueError('Certificate/private key mismatch')
            issued[name] = cert
        for namespace in namespaces:
            secret = json.loads(command(k + ['get', 'secret', 'gateway-tls', '-n', namespace, '-o', 'json']))
            if base64.b64decode(secret['data']['tls.key']) != (private / 'server.key').read_bytes(): raise ValueError('Preserve an independently changed gateway key')
            secrets[namespace] = secret['data']
        (work / 'original-tls-secrets.json').write_text(json.dumps(secrets))
        # These are operational trust/certificate files, not historical docs/evidence capsules.
        # Every previous certificate is kept above; no private key, receipt, model or feature clock is edited.
        atomic_replace(authority, ca.read_bytes()); atomic_replace(vault_ca, ca.read_bytes())
        atomic_replace(gateway / 'server.crt', issued['gateway'].read_bytes()); atomic_replace(vault / 'server.crt', issued['vault'].read_bytes())
        for namespace in namespaces:
            patch = {'data': {'tls.crt': base64.b64encode(issued['gateway'].read_bytes()).decode()}}
            command(k + ['patch', 'secret', 'gateway-tls', '-n', namespace, '--type=merge', '--patch-file=/dev/stdin'], json.dumps(patch).encode())
        command(['systemctl', 'restart', 'iot-vault-28'])
        healthy = activate(vault_ca)
        if healthy['cluster_id'] != args.expected_cluster_id: raise ValueError('Vault persistent cluster identity changed')
        address = json.loads(command(k + ['get', 'service', 'iot-gateway', '-n', 'iot-gateway', '-o', 'json']))['spec']['clusterIP']
        credentials = json.loads((private / 'credentials.json').read_bytes()); context = ssl.create_default_context(cafile=str(ca))
        authorization = 'Basic ' + base64.b64encode((credentials['username'] + ':' + credentials['password']).encode()).decode(); credentials = None
        results = {}; peers = {}; expected_peer = sha256(command(['openssl', 'x509', '-in', str(issued['gateway']), '-outform', 'DER'])).hexdigest()
        for host, path in [('metrics.iot.test', '/api/health'), ('logs.iot.test', '/api/health'), ('traces.iot.test', '/api/v3/services'), ('features.iot.test', '/health/ready')]:
            for attempt in range(30):
                try:
                    client = http.client.HTTPSConnection(address, 443, context=context, timeout=10)
                    client.connect = lambda: setattr(client, 'sock', context.wrap_socket(socket.create_connection((address, 443), timeout=10), server_hostname=host))
                    client.connect(); peer = sha256(client.sock.getpeercert(binary_form=True)).hexdigest()
                    if peer != expected_peer:
                        client.close(); time.sleep(1); continue
                    peers[host] = peer
                    client.request('GET', path, headers={'Host': host, 'Authorization': authorization}); response = client.getresponse()
                    results[host] = response.status; response.read(); client.close(); break
                except ssl.SSLCertVerificationError:
                    if attempt == 29: raise
                    time.sleep(1)
            if host not in results: raise ValueError('Gateway did not load the renewed certificate')
        authorization = None
        if any(sha256((root / name).read_bytes()).hexdigest() != pin for name, pin in key_hashes.items()): raise ValueError('Unexpected private key change')
        report.update(passed=True, checked_utc=datetime.now(timezone.utc).isoformat(), after={str(path.relative_to(root)): certificate(path) for path in paths},
                      vault_cluster_id=healthy['cluster_id'], vault_health_http=200, vault_reinitialized=False, gateway_http=results,
                      actual_gateway_peer_der_sha256=peers, expected_gateway_peer_der_sha256=expected_peer,
                      gateway_all_routes_200=all(code == 200 for code in results.values()), private_key_bytes_unchanged=True,
                      scope='New real X509 issuance: CA14days/server7days, same protected keys and persistent services. Historical evidence untouched; application readiness remains a separate check.')
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        if any(path.read_bytes() != raw for path, raw in backup.items()):
            for path, raw in backup.items(): atomic_replace(path, raw)
            for namespace, data in secrets.items(): command(k + ['patch', 'secret', 'gateway-tls', '-n', namespace, '--type=merge', '--patch-file=/dev/stdin'], json.dumps({'data': data}).encode())
            command(['systemctl', 'restart', 'iot-vault-28'])
            try: activate(vault_ca); report['restored_original_certificates'] = True
            except Exception: report['restored_original_certificates'] = False
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    for name in ['ca', 'gateway', 'vault']:
        path = work / (name + '.crt')
        if path.is_file(): (output / (name + '.crt')).write_bytes(path.read_bytes())
    print(json.dumps(report, indent=2)); return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--run', required=True); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-cluster-id', default='63e1d9cc-7eb1-c7a7-a48d-b11a3ac107f9')
    raise SystemExit(run(parser.parse_args()))
