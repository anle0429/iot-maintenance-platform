"""Open retained monitoring UIs through loopback IAP and the CA-verified, authenticated private NGINX gateway."""
from datetime import datetime,timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit
import argparse,base64,http.client,json,socket,ssl,threading

HOSTS={'metrics':'metrics.iot.test','logs':'logs.iot.test','traces':'traces.iot.test'}
PORTS={'metrics':18100,'logs':18101,'traces':18102}


def allowed(role,host,origin,path,method):
    """Reject proxy URLs, foreign origins and administrative writes before adding the private operator credential."""
    if role not in HOSTS:return False
    hosts={'127.0.0.1:'+str(PORTS[role]),'localhost:'+str(PORTS[role])}
    if host not in hosts or origin and origin not in {'http://'+v for v in hosts}:return False
    if not path.startswith('/') or path.startswith('//') or '\\' in path or urlsplit(path).scheme:return False
    if method in {'GET','HEAD'}:return True
    return role in {'metrics','logs'} and method=='POST' and urlsplit(path).path in {'/api/ds/query','/api/frontend-metrics'}


def run(args):
    """Relay one fixed read-only viewer; audit actual TLS/response hashes without logging request bodies or authentication."""
    role=args.role;host=HOSTS[role];port=PORTS[role];address=ip_address(args.gateway_ip)
    assert address.version==4 and address.is_private and not (address.is_loopback or address.is_unspecified or address.is_multicast)
    credentials=json.loads(args.credentials.read_bytes());assert credentials['username']=='coursework' and len(credentials['password'])>=32
    auth='Basic '+base64.b64encode((credentials['username']+':'+credentials['password']).encode()).decode();credentials=None
    context=ssl.create_default_context(cafile=str(args.ca));assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED
    args.audit.parent.mkdir(parents=True,exist_ok=True);audit=args.audit.open('x');lock=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        """Preserve native viewer response bytes while constraining the credential-bearing upstream destination."""
        def log_message(self,*values):
            """Keep URLs and sensitive headers out of server diagnostics."""
            return
        def forward(self):
            """Attach the existing gateway credential only after local origin, method and target checks."""
            if not allowed(role,self.headers.get('Host',''),self.headers.get('Origin'),self.path,self.command):self.send_error(403);return
            try:length=int(self.headers.get('Content-Length','0'))
            except ValueError:self.send_error(400);return
            if not 0<=length<=1048576:self.send_error(413);return
            body=self.rfile.read(length) if length else None
            peer=context.wrap_socket(socket.create_connection((str(address),443),timeout=15),server_hostname=host);peer.settimeout(60)
            leaf=sha256(peer.getpeercert(binary_form=True)).hexdigest();version=peer.version()
            connection=http.client.HTTPSConnection(str(address),443,context=context,timeout=60);connection.sock=peer
            blocked={'host','authorization','origin','accept-encoding','content-length','connection','transfer-encoding','keep-alive','forwarded','x-real-ip','x-user-id'}
            headers={n:v for n,v in self.headers.items() if n.lower() not in blocked and not n.lower().startswith('x-forwarded-')}
            headers.update({'Host':host,'Authorization':auth,'Origin':'https://'+host})
            connection.request(self.command,self.path,body=body,headers=headers);response=connection.getresponse();self.send_response(response.status)
            for n,v in response.getheaders():
                if n.lower() in {'connection','transfer-encoding','keep-alive','proxy-authenticate','proxy-authorization','upgrade'}:continue
                if n.lower()=='location' and urlsplit(v).hostname==host:
                    location=urlsplit(v);v=location.path+('?' + location.query if location.query else '')
                self.send_header(n,v)
            self.send_header('Connection','close');self.end_headers();digest=sha256();size=0
            try:
                if self.command!='HEAD':
                    while True:
                        chunk=response.read1(16384)
                        if not chunk:break
                        self.wfile.write(chunk);self.wfile.flush();digest.update(chunk);size+=len(chunk)
            finally:
                connection.close();self.close_connection=True
                with lock:
                    audit.write(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),'host':host,'path':urlsplit(self.path).path,
                        'method':self.command,'status':response.status,'ca_verified':True,'hostname_verified':True,'tls_version':version,
                        'leaf_sha256':leaf,'response_bytes':size,'served_response_sha256':digest.hexdigest(),'body_edited':False})+'\n');audit.flush()
        do_GET=forward
        do_HEAD=forward
        do_POST=forward
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(json.dumps({'listen':'http://127.0.0.1:'+str(port),'upstream':'https://'+host,'scope':'Loopback administrative view over IAP; upstream CA/SNI/auth verified. Browser does not establish private HTTPS.'}),flush=True)
    try:server.serve_forever()
    finally:server.server_close();audit.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--role',choices=sorted(HOSTS),required=True);parser.add_argument('--gateway-ip',required=True)
    for name in ['credentials','ca','audit']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
