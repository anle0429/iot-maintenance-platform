"""Deploy the private coursework gateway once, retaining credentials only in a private runtime folder."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import argparse,base64,ipaddress,json,os,secrets,subprocess,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from infra.gateway.resources import resources,HOSTS,NAMESPACE


def run(args):
    """Require an approved active VM, fresh output/private paths and unchanged private backends before apply."""
    os.umask(0o077)
    root=args.runtime_root.resolve();session=json.loads((root/'data/runtime/ci/session.json').read_bytes())
    if datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc)<__import__('datetime').timedelta(minutes=30):raise ValueError('Need30minutes in the approved session')
    if not root.is_relative_to('/srv/iot-data') or not Path('/srv/iot-fast').is_mount():raise ValueError('Use existing VM data disks')
    out=args.output.resolve();private=args.private.resolve();out.mkdir(parents=True,exist_ok=False);private.mkdir(parents=True,exist_ok=False)
    def save(n,v):
        'Save strict JSON deployment evidence without nonfinite values.'
        (out/n).write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')
    def call(command,stdin=None,log=None):
        'Run a bounded gateway command and retain its original streams when requested.'
        p=subprocess.run(command,input=stdin,capture_output=True,timeout=300)
        if log:(out/log).write_bytes(p.stdout+p.stderr)
        if p.returncode:raise RuntimeError('Gateway native command failed; see retained logs: '+str(log or command[0]))
        return p.stdout
    k=['/usr/local/bin/k3s','kubectl'];get=lambda *a:json.loads(call(k+list(a)+['-o','json']))
    if subprocess.run(k+['get','namespace',NAMESPACE],capture_output=True).returncode==0:raise ValueError('Gateway namespace already exists; inspect before any update')
    services={n:get('get','service',name,'-n',ns) for n,name,ns in [('features','iot-feature-api','iot-features'),('traces','iot-jaeger','iot-observability')]}
    for service in services.values():
        if service['spec'].get('type','ClusterIP')!='ClusterIP' or service['spec'].get('externalIPs'):raise ValueError('Backend must remain private')
    net=json.loads(call(['ip','-j','-4','address','show','cni0']))[0]['addr_info'];bridge=next(x['local'] for x in net if x['family']=='inet');assert ipaddress.ip_address(bridge).is_private
    image=json.loads((ROOT/'infra/gateway/assets.json').read_bytes())['image']
    save('before.json',{'utc':datetime.now(timezone.utc).isoformat(),'session':session,'services':services,'bridge_ip':bridge,'source_sha256':{p:sha256((ROOT/p).read_bytes()).hexdigest() for p in ['infra/gateway/resources.py','infra/gateway/assets.json','scripts/deploy-private-gateway.py']}})
    # systemd's standard byte relay connects only the CNI bridge to the existing loopback Grafana.
    proxy=next(p for p in ['/usr/lib/systemd/systemd-socket-proxyd','/lib/systemd/systemd-socket-proxyd'] if Path(p).is_file())
    unitdir=Path('/run/systemd/system')
    socket='[Unit]\nDescription=IoT gateway private CNI Grafana socket\n[Socket]\nListenStream='+bridge+':13002\nNoDelay=true\n[Install]\nWantedBy=sockets.target\n'
    service='[Unit]\nDescription=IoT gateway loopback Grafana relay\n[Service]\nExecStart='+proxy+' 127.0.0.1:13001\nUser=nobody\nNoNewPrivileges=true\nProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\nMemoryMax=32M\nCPUQuota=10%\n'
    for suffix,text in [('socket',socket),('service',service)]:
        p=unitdir/('iot-gateway-grafana-28.'+suffix)
        if p.exists():raise ValueError('Relay unit exists; refuse overwrite')
        p.write_text(text);(out/('relay.'+suffix)).write_text(text)
    call(['systemctl','daemon-reload']);call(['systemctl','start','iot-gateway-grafana-28.socket'])
    save('relay-listener.json',{'listener':bridge+':13002','upstream':'127.0.0.1:13001','public':False,'mechanism':'systemd-socket-proxyd','scope':'CNI-private bridge; backends remain trusted internal; no public listener or new cloud firewall.'})
    # Explicit CA and hostname verification are required; no OS trust-store changes or insecure client flags.
    def ssl(*a):'Run OpenSSL through the checked command path and retain the latest TLS operation log.';return call(['openssl',*a],log='tls-generation-last.txt')
    ssl('req','-x509','-newkey','rsa:3072','-nodes','-keyout',str(private/'ca.key'),'-out',str(out/'ca.crt'),'-days','3','-subj','/CN=IoT coursework private lab CA','-addext','basicConstraints=critical,CA:TRUE','-addext','keyUsage=critical,keyCertSign,cRLSign')
    ssl('req','-new','-newkey','rsa:3072','-nodes','-keyout',str(private/'server.key'),'-out',str(private/'server.csr'),'-subj','/CN=features.iot.test')
    (private/'extensions.cnf').write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName='+','.join('DNS:'+h for h in HOSTS.values())+'\n')
    ssl('x509','-req','-in',str(private/'server.csr'),'-CA',str(out/'ca.crt'),'-CAkey',str(private/'ca.key'),'-CAcreateserial','-out',str(out/'server.crt'),'-days','2','-extfile',str(private/'extensions.cnf'))
    password=secrets.token_urlsafe(32);(private/'credentials.json').write_text(json.dumps({'username':'coursework','password':password})+'\n')
    encoded=call(['openssl','passwd','-6','-stdin'],stdin=(password+'\n').encode()).decode().strip()
    htpasswd='coursework:'+encoded+'\n';password=None
    docs=resources(image,bridge);(out/'manifests.json').write_text(json.dumps({'apiVersion':'v1','kind':'List','items':docs},indent=2)+'\n')
    call(k+['create','namespace',NAMESPACE],log='namespace.txt')
    for ns in [NAMESPACE,'iot-features','iot-observability']:
        for name,typ,data in [('gateway-auth','Opaque',{'htpasswd':htpasswd}),('gateway-tls','kubernetes.io/tls',{'tls.crt':(out/'server.crt').read_text(),'tls.key':(private/'server.key').read_text()})]:
            doc={'apiVersion':'v1','kind':'Secret','metadata':{'name':name,'namespace':ns},'type':typ,'stringData':data}
            # Create avoids storing plaintext last-applied annotations on Secret objects.
            call(k+['create','-f','-'],stdin=json.dumps(doc).encode())
    call(k+['apply','-f',str(out/'manifests.json')],log='apply.txt')
    call(k+['rollout','status','deployment/iot-gateway','-n',NAMESPACE,'--timeout=240s'],log='rollout.txt')
    save('deployment.json',get('get','deployment','iot-gateway','-n',NAMESPACE));save('pods.json',get('get','pods','-n',NAMESPACE));save('service.json',get('get','service','iot-gateway','-n',NAMESPACE))
    for ns in [NAMESPACE,'iot-features','iot-observability']:
        save('ingress-'+ns+'.json',get('get','ingress','-n',ns));save('events-'+ns+'.json',get('get','events','-n',ns))
    save('ready.json',{'utc':datetime.now(timezone.utc).isoformat(),'ready':True,'image':image,'scope':'Private gateway only; successful backend data/auth/rate/TLS checks recorded separately. No public DNS/Internet endpoint.'})
    print(json.dumps({'ready':True,'output':str(out),'private_credentials':str(private/'credentials.json')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output','private']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
