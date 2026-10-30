"""View the retained native Vault UI through private verified TLS, with secret-value reads and mutations blocked."""
from datetime import datetime, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
import argparse, http.client, json, ssl, threading

ADDRESS = '10.52.0.1'
UPSTREAM_PORT = 18200
PORT = 21950
AGENT_PATHS = {'iot/metadata', 'iot/metadata/agents', 'iot/metadata/agents/gateway', 'iot/metadata/agents/services'}
UI_READS = {'/v1/auth/token/lookup-self', '/v1/sys/health', '/v1/sys/internal/ui/mounts',
            '/v1/sys/internal/ui/resultant-acl', '/v1/sys/internal/ui/feature-flags',
            '/v1/sys/license/features', '/v1/sys/replication/status'}


def allowed_request(host, origin, target, method, port=PORT):
    """Admit native UI assets and explicit agent metadata only; never forward a secret-data read or write."""
    hosts = {'127.0.0.1:'+str(port), 'localhost:'+str(port)}
    if host not in hosts or origin and origin not in {'http://'+v for v in hosts}: return False
    value = urlsplit(target); path = value.path
    if value.scheme or value.netloc or value.fragment or unquote(path) != path or '\\' in path or '..' in path.split('/') or '//' in path: return False
    if path == '/ui' or path.startswith('/ui/'):
        return method in {'GET', 'HEAD'}
    if method == 'POST': return path == '/v1/sys/capabilities-self' and not value.query
    if method not in {'GET', 'HEAD', 'LIST'}: return False
    if path in UI_READS: return method != 'LIST' and not value.query
    if path.startswith('/v1/sys/internal/ui/mounts/'):
        return method != 'LIST' and not value.query and path.removeprefix('/v1/sys/internal/ui/mounts/').rstrip('/') in {
            'iot', 'iot/agents', 'iot/agents/gateway', 'iot/agents/services'}
    if path.removeprefix('/v1/').rstrip('/') not in AGENT_PATHS: return False
    return not value.query or parse_qs(value.query, keep_blank_values=True) == {'list': ['true']}


def allowed_body(body):
    """Allow only a bounded permission lookup, including data-path permissions without reading their values."""
    try: value = json.loads(body)
    except (ValueError, UnicodeError): return False
    if not isinstance(value, dict): return False
    paths = value.get('paths')
    allowed = AGENT_PATHS | {'iot/data/agents/gateway', 'iot/data/agents/services'}
    return set(value) == {'paths'} and isinstance(paths, list) and 0 < len(paths) <= 8 and all(isinstance(v, str) and v.rstrip('/') in allowed for v in paths)


def run(args):
    """Forward unchanged native UI/metadata over fixed TLS; keep credentials and response bodies out of the audit."""
    context = ssl.create_default_context(cafile=str(args.ca))
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    args.audit.parent.mkdir(parents=True, exist_ok=True); audit = args.audit.open('x', encoding='utf-8'); lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        """Constrain the local administrative browser before forwarding its existing Vault login."""

        def log_message(self, *values):
            """Suppress token headers, request bodies and potential URL diagnostics."""
            return

        def forward(self):
            """Apply method/path/origin/body guards and record safe transport facts for accepted requests."""
            if len(self.headers.get_all('Host', [])) != 1 or not allowed_request(self.headers.get('Host', ''), self.headers.get('Origin', ''), self.path, self.command, args.port):
                self.send_error(403); return
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) > 1 or self.headers.get('Transfer-Encoding'): self.send_error(400); return
            declared = lengths[0] if lengths else '0'
            if not declared.isascii() or not declared.isdigit() or len(declared) > 6 or int(declared) > 16384:
                self.send_error(413); return
            body = self.rfile.read(int(declared)) if int(declared) else None
            if self.command == 'POST' and (body is None or not allowed_body(body)): self.send_error(403); return
            connection = http.client.HTTPSConnection(ADDRESS, UPSTREAM_PORT, context=context, timeout=30)
            connection.connect(); leaf = sha256(connection.sock.getpeercert(binary_form=True)).hexdigest(); version = connection.sock.version()
            headers = {'Host': ADDRESS+':'+str(UPSTREAM_PORT), 'Accept': self.headers.get('Accept', '*/*'), 'Accept-Encoding': 'identity'}
            for name in ['Content-Type', 'X-Vault-Token', 'X-Vault-Request', 'X-Vault-Namespace']:
                if self.headers.get(name): headers[name] = self.headers[name]
            connection.request(self.command, self.path, body=body, headers=headers); response = connection.getresponse()
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in {'connection', 'transfer-encoding', 'keep-alive', 'location'}: self.send_header(name, value)
                elif name.lower() == 'location' and value.startswith('/ui'): self.send_header(name, value)
            self.send_header('Connection', 'close'); self.end_headers(); digest = sha256(); size = 0
            try:
                if self.command != 'HEAD':
                    while True:
                        chunk = response.read1(16384)
                        if not chunk: break
                        self.wfile.write(chunk); digest.update(chunk); size += len(chunk)
            finally:
                connection.close(); self.close_connection = True
                with lock:
                    audit.write(json.dumps({'utc': datetime.now(timezone.utc).isoformat(), 'path': urlsplit(self.path).path,
                        'method': self.command, 'status': response.status, 'ca_verified': True, 'hostname_verified': True,
                        'tls_version': version, 'leaf_sha256': leaf, 'response_bytes': size,
                        'response_sha256': digest.hexdigest(), 'body_edited': False, 'credentials_logged': False})+'\n'); audit.flush()

        do_GET = forward
        do_HEAD = forward
        do_LIST = forward
        do_POST = forward
        do_PUT = forward
        do_DELETE = forward
        do_PATCH = forward

    if not 1024 <= args.port <= 65535: raise ValueError('Use a nonprivileged local viewer port')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(json.dumps({'listen': 'http://127.0.0.1:'+str(args.port), 'upstream': 'https://'+ADDRESS+':'+str(UPSTREAM_PORT),
        'scope': 'Native Vault UI; agent metadata only; all secret-data reads and mutations denied by this administrative relay.'}), flush=True)
    try: server.serve_forever()
    finally: server.server_close(); audit.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ca', type=Path, required=True); parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--port', type=int, default=PORT); run(parser.parse_args())
