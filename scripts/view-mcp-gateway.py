"""Show the native retrieval API through a loopback-only administrative relay that verifies private upstream HTTPS."""
from datetime import datetime, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import unquote, urlsplit
import argparse, base64, http.client, json, re, socket, ssl, threading

HOST = 'retrieval.iot.test'
PORT = 19110


def allowed_request(host, origin, path, method):
    """Reject foreign origins, proxy targets, path aliases and operations outside the fixed retrieval API."""
    hosts = {'127.0.0.1:'+str(PORT), 'localhost:'+str(PORT)}
    if host not in hosts or origin and origin not in {'http://'+v for v in hosts}: return False
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or unquote(path) != path or '\\' in path: return False
    if method in {'GET', 'HEAD'}:
        return path in {'/docs', '/docs/oauth2-redirect', '/openapi.json', '/health/live', '/health/ready'} or bool(re.fullmatch(r'/v1/machines/P-[0-9]{3,9}/features', path))
    return method == 'POST' and (path == '/v1/knowledge:search' or bool(re.fullmatch(r'/v1/machines/P-[0-9]{3,9}:predict', path)))


def run(args):
    """Use credentials in memory, stream native response bytes and record only safe transport/provenance facts."""
    address = ip_address(args.gateway_ip)
    if address.version != 4 or not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast:
        raise ValueError('Use the retained private gateway ClusterIP')
    credentials = json.loads(args.credentials.read_bytes()); token = json.loads(args.mcp_tokens.read_bytes())['retrieval']
    assert credentials['username'] == 'coursework' and len(credentials['password']) >= 32 and len(token) >= 32
    basic = 'Basic '+base64.b64encode((credentials['username']+':'+credentials['password']).encode()).decode()
    context = ssl.create_default_context(cafile=str(args.ca)); lock = threading.Lock()
    args.audit.parent.mkdir(parents=True, exist_ok=True); audit = args.audit.open('x', encoding='utf-8')

    class Handler(BaseHTTPRequestHandler):
        """Serve only native documentation and bounded read/predict/search operations for the local operator."""
        protocol_version = 'HTTP/1.1'

        def log_message(self, *values):
            """Suppress request diagnostics that might reveal caller text or authorization fields."""
            return

        def forward(self):
            """Check the local browser boundary before adding credentials to one fixed CA/SNI-verified peer."""
            if len(self.headers.get_all('Host', [])) != 1 or not allowed_request(self.headers.get('Host', ''), self.headers.get('Origin', ''), self.path, self.command):
                self.send_error(403); return
            lengths = self.headers.get_all('Content-Length', [])
            if self.headers.get('Transfer-Encoding') or len(lengths) > 1:
                self.send_error(400); return
            declared = lengths[0] if lengths else '0'
            if not declared.isascii() or not declared.isdigit(): self.send_error(400); return
            if len(declared) > 8 or int(declared) > 65536: self.send_error(413); return
            if self.command == 'POST' and not lengths: self.send_error(411); return
            body = self.rfile.read(int(declared)) if int(declared) else None
            peer = context.wrap_socket(socket.create_connection((str(address), 443), timeout=15), server_hostname=HOST)
            peer.settimeout(90); leaf = sha256(peer.getpeercert(binary_form=True)).hexdigest(); version = peer.version()
            connection = http.client.HTTPSConnection(str(address), 443, context=context, timeout=90); connection.sock = peer
            headers = {'Host': HOST, 'Authorization': basic, 'X-MCP-Authorization': 'Bearer '+token,
                       'Accept': self.headers.get('Accept', 'application/json')}
            if self.headers.get('Content-Type'): headers['Content-Type'] = self.headers['Content-Type']
            connection.request(self.command, self.path, body=body, headers=headers); response = connection.getresponse()
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in {'connection', 'transfer-encoding', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'upgrade'}:
                    self.send_header(name, value)
            self.send_header('Connection', 'close'); self.end_headers(); digest = sha256(); size = 0
            try:
                if self.command != 'HEAD':
                    while True:
                        chunk = response.read1(16384)
                        if not chunk: break
                        self.wfile.write(chunk); self.wfile.flush(); digest.update(chunk); size += len(chunk)
            finally:
                connection.close(); self.close_connection = True
                record = {'utc': datetime.now(timezone.utc).isoformat(), 'host': HOST, 'path': self.path, 'method': self.command,
                          'status': response.status, 'ca_verified': True, 'hostname_verified': True, 'tls_version': version,
                          'leaf_sha256': leaf, 'response_bytes': size, 'served_response_sha256': digest.hexdigest(),
                          'body_edited': False, 'credentials_logged': False}
                with lock: audit.write(json.dumps(record)+'\n'); audit.flush()

        do_GET = forward
        do_HEAD = forward
        do_POST = forward

    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print(json.dumps({'listen': 'http://127.0.0.1:'+str(PORT), 'upstream': 'https://'+HOST, 'ca_verified': True,
                      'scope': 'Administrative loopback HTTP relay over IAP; upstream is verified HTTPS. No browser CA bypass or public listener.'}), flush=True)
    try: server.serve_forever()
    finally: server.server_close(); audit.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--gateway-ip', required=True)
    for name in ['credentials', 'mcp-tokens', 'ca', 'audit']: parser.add_argument('--'+name, type=Path, required=True)
    run(parser.parse_args())
