"""View the private agent gateway through a loopback-only, CA/SNI-verifying administrative relay over IAP."""
from datetime import datetime,timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit
import argparse,base64,http.client,json,re,socket,ssl,threading

HOSTS={'agents':'agents.iot.test','registry':'registry.iot.test'}
PORTS={'agents':18090,'registry':18091}


def upstream_headers(incoming,host,authorization):
    """Preserve application protocol headers, including Next.js segment-prefetch hashes, while replacing trust/routing fields."""
    blocked={'host','authorization','origin','accept-encoding','content-length','connection','transfer-encoding','keep-alive',
             'proxy-authenticate','proxy-authorization','upgrade','te','trailer','forwarded','x-real-ip','x-user-id'}
    result={name:value for name,value in incoming.items() if name.lower() not in blocked and not name.lower().startswith('x-forwarded-')}
    result.update({'Host':host,'Authorization':authorization,'Origin':'https://'+host})
    return result


def allowed_request(role,host,origin,path,method):
    """Refuse absolute proxy URLs, foreign browser origins and operations outside this bounded administrative view."""
    if role not in HOSTS:return False
    port=PORTS[role];valid_hosts={'127.0.0.1:'+str(port),'localhost:'+str(port)}
    if host not in valid_hosts or origin and origin not in {'http://'+value for value in valid_hosts}:return False
    if not path.startswith('/') or path.startswith('//') or '\\' in path or urlsplit(path).scheme:return False
    chat_action=re.fullmatch(r'/agents/iot-agents/iot-(retrieval|drift|coordinator)/chat(?:/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})?',urlsplit(path).path)
    return method in {'GET','HEAD'} or role=='agents' and method=='POST' and (path.startswith('/api/') or bool(chat_action))


def run(args):
    """Authenticate upstream in memory, preserve application response bytes and record no password, header or request body."""
    role=args.role;port=PORTS[role];host=HOSTS[role];address=ip_address(args.gateway_ip)
    if address.version!=4 or not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast:
        raise ValueError('Use only the actual private gateway ClusterIP')
    credentials=json.loads(args.credentials.read_bytes());assert credentials['username']=='coursework' and len(credentials['password'])>=32
    auth='Basic '+base64.b64encode((credentials['username']+':'+credentials['password']).encode()).decode()
    context=ssl.create_default_context(cafile=str(args.ca));lock=threading.Lock();args.audit.parent.mkdir(parents=True,exist_ok=True)
    audit=args.audit.open('x',encoding='utf-8')
    class Handler(BaseHTTPRequestHandler):
        """Forward only the selected native UI; stream unchanged bodies while verifying its private HTTPS identity."""
        protocol_version='HTTP/1.1'

        def log_message(self,*values):
            """Suppress default request diagnostics so credentials and query values never enter the console."""
            return

        def forward(self):
            """Enforce local origin/path/method bounds before attaching the existing operator login to the fixed peer."""
            if not allowed_request(role,self.headers.get('Host',''),self.headers.get('Origin',''),self.path,self.command):
                self.send_error(403);return
            try:length=int(self.headers.get('Content-Length','0'))
            except ValueError:self.send_error(400);return
            if not 0<=length<=1048576:self.send_error(413);return
            body=self.rfile.read(length) if length else None
            peer=context.wrap_socket(socket.create_connection((str(address),443),timeout=15),server_hostname=host);peer.settimeout(660)
            leaf=sha256(peer.getpeercert(binary_form=True)).hexdigest();version=peer.version()
            connection=http.client.HTTPSConnection(str(address),443,context=context,timeout=660);connection.sock=peer
            headers=upstream_headers(self.headers,host,auth)
            connection.request(self.command,self.path,body=body,headers=headers);response=connection.getresponse()
            self.send_response(response.status)
            for name,value in response.getheaders():
                if name.lower() in {'connection','transfer-encoding','keep-alive','proxy-authenticate','proxy-authorization','upgrade'}:continue
                if name.lower()=='location':
                    location=urlsplit(value)
                    if location.scheme and location.hostname==host:value=location.path+('?' + location.query if location.query else '')
                self.send_header(name,value)
            self.send_header('Connection','close');self.end_headers();digest=sha256();size=0;debug=[]
            try:
                if self.command!='HEAD':
                    while True:
                        chunk=response.read1(16384)
                        if not chunk:break
                        self.wfile.write(chunk);self.wfile.flush();digest.update(chunk);size+=len(chunk)
                        if args.debug_actions and self.command=='POST' and size<=1048576:debug.append(chunk)
            finally:
                connection.close();self.close_connection=True
                record={'utc':datetime.now(timezone.utc).isoformat(),'host':host,'path':urlsplit(self.path).path,'method':self.command,
                        'status':response.status,'ca_verified':True,'hostname_verified':True,'tls_version':version,'leaf_sha256':leaf,
                        'response_bytes':size,'served_response_sha256':digest.hexdigest(),'body_edited':False,'credentials_logged':False}
                with lock:audit.write(json.dumps(record)+'\n');audit.flush()
                if args.debug_actions and self.command=='POST':
                    target=args.debug_actions/(datetime.now(timezone.utc).strftime('%H%M%S%f')+'-'+self.headers.get('Next-Action','api')[:16]+'.json')
                    target.write_text(json.dumps({'path':urlsplit(self.path).path,'next_action':self.headers.get('Next-Action'),
                        'request_body':body.decode() if body else None,'response_body':b''.join(debug).decode(errors='replace')},indent=2)+'\n')

        do_GET=forward
        do_HEAD=forward
        do_POST=forward
    if args.debug_actions:
        if not args.debug_actions.resolve().is_relative_to(Path('/srv/iot-data/workspace/iot-maintenance-platform/data/runtime')):
            raise ValueError('Server-action diagnostics must remain in the private runtime directory')
        args.debug_actions.mkdir(parents=True,exist_ok=False);args.debug_actions.chmod(0o700)
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(json.dumps({'listen':'http://127.0.0.1:'+str(port),'upstream':'https://'+host,'ca_verified':True,
                      'scope':'Authenticated administrative relay; browser sees loopback HTTP. No browser CA bypass, public listener or external proxy target.'}),flush=True)
    try:server.serve_forever()
    finally:server.server_close();audit.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--role',choices=sorted(HOSTS),required=True);parser.add_argument('--gateway-ip',required=True)
    for name in ['credentials','ca','audit']:parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--debug-actions',type=Path)
    run(parser.parse_args())
