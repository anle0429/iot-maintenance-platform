"""Add two private agent UI routes to the existing NGINX gateway using a dedicated Vault-managed login."""
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from pathlib import Path
import argparse,base64,json,os,socket,ssl,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from infra.gateway.agents import controller_patch,access_resources,ingresses,ROUTES
from infra.security.vault_client import VaultClient


def run(args):
    """Preserve the existing routes, model and workers; patch only the guarded namespace scope and new named resources."""
    os.umask(0o077);root=args.runtime_root.resolve();out=args.output.resolve();private=args.private.resolve()
    if not root.is_relative_to('/srv/iot-data') or not out.is_relative_to(root/'outputs') or not private.is_relative_to(root/'data/runtime'):
        raise ValueError('Use only project runtime paths on the retained VM')
    session=json.loads((root/'data/runtime/ci/session.json').read_bytes())
    if datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc)<timedelta(minutes=30):raise ValueError('Require thirty actual CPU session minutes')
    out.mkdir(parents=True,exist_ok=False);private.mkdir(parents=True,exist_ok=False)
    def save(name,value):
        """Preserve safe native objects and readiness/source facts without Secret bodies."""
        (out/name).parent.mkdir(parents=True,exist_ok=True);(out/name).write_text(json.dumps(value,indent=2)+'\n')
    def command(values,payload=None,log=None,timeout=60):
        """Keep command output separate from any credential-bearing input and suppress unexpected failure details."""
        result=subprocess.run(values,input=payload,capture_output=True,timeout=timeout)
        if log:(out/log).write_bytes(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError('Native gateway command failed; inspect '+str(log or 'the private operator output'))
        return result.stdout
    k=['/usr/local/bin/k3s','kubectl']
    def get(*values):
        """Read one actual Kubernetes object through the bounded command path."""
        return json.loads(command(k+list(values)+['-o','json']))
    deployment=get('get','deployment','iot-gateway','-n','iot-gateway');image=json.loads((ROOT/'infra/gateway/assets.json').read_bytes())['image']
    patch=controller_patch(deployment,image)
    if not patch:raise ValueError('Agent namespace already watched; inspect the previous release before updating it')
    tracked={'gateway':deployment,'old_ingresses':get('get','ingresses','--all-namespaces'),
        'agents':get('get','sandboxagents','-n','iot-agents'),'pools':get('get','workerpools','-n','iot-agents'),
        'model':get('get','modelconfig','default-model-config','-n','iot-agents')}
    for name,value in tracked.items():save('before-'+name+'.json',value)
    for kind,name in [('secret','agent-gateway-auth'),('secret','agent-gateway-tls'),('role','iot-agent-gateway-read'),
                      ('rolebinding','iot-agent-gateway-read'),('ingress','iot-agent-agents'),('ingress','iot-agent-registry')]:
        raw=command(k+['get',kind,name,'-n','iot-agents','--ignore-not-found','-o','name'])
        if raw.strip():raise ValueError('Preserve an existing named agent gateway resource: '+name)
    for route,(_,service,_) in ROUTES.items():
        value=get('get','service',service,'-n','iot-agents')
        if value['spec']['type']!='ClusterIP' or value['spec'].get('externalIPs'):raise ValueError('Keep agent frontends private')
        save('backend-'+route+'.json',value)
    client=VaultClient('https://10.52.0.1:18200',args.ca,json.loads((args.vault_private/'gateway-approle.json').read_bytes()))
    lease=client.login();credentials,version=client.read('iot/data/agents/gateway');client.revoke()
    assert lease['token_policies']==['iot-agent-gateway'] and set(credentials)=={'username','password'} and credentials['username']=='coursework'
    (private/'credentials.json').write_text(json.dumps(credentials)+'\n')
    encoded=command(['openssl','passwd','-6','-stdin'],payload=(credentials['password']+'\n').encode()).decode().strip()
    htpasswd=credentials['username']+':'+encoded+'\n'
    (out/'ca.crt').write_bytes(args.ca.read_bytes())
    command(['openssl','x509','-in',str(args.ca),'-checkend','3600','-noout'],log='ca-validity.txt')
    command(['openssl','req','-new','-newkey','rsa:3072','-nodes','-keyout',str(private/'server.key'),'-out',str(private/'server.csr'),
             '-subj','/CN=agents.iot.test'],log='tls-request.txt')
    extensions='basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName='+','.join('DNS:'+r[0] for r in ROUTES.values())+'\n'
    (private/'extensions.cnf').write_text(extensions)
    command(['openssl','x509','-req','-in',str(private/'server.csr'),'-CA',str(args.ca),'-CAkey',str(root/'data/runtime/gateway-28-private/ca.key'),
             '-set_serial','0x'+os.urandom(16).hex(),'-out',str(out/'server.crt'),'-days','2','-extfile',str(private/'extensions.cnf')],log='tls-sign.txt')
    command(['openssl','verify','-CAfile',str(args.ca),str(out/'server.crt')],log='tls-verify.txt')
    for name,typ,data in [('agent-gateway-auth','Opaque',{'htpasswd':htpasswd}),
                          ('agent-gateway-tls','kubernetes.io/tls',{'tls.crt':(out/'server.crt').read_text(),'tls.key':(private/'server.key').read_text()})]:
        doc={'apiVersion':'v1','kind':'Secret','metadata':{'name':name,'namespace':'iot-agents'},'type':typ,'stringData':data}
        command(k+['create','-f','-'],payload=json.dumps(doc).encode())
        observed=get('get','secret',name,'-n','iot-agents')
        save(name+'-identity.json',{'uid':observed['metadata']['uid'],'type':observed['type'],'keys':sorted(observed['data']),
                                  'vault_secret_version':version if name.endswith('auth') else None})
    docs=access_resources()+ingresses();save('manifests.json',{'apiVersion':'v1','kind':'List','items':docs});save('controller-patch.json',patch)
    command(k+['apply','--dry-run=server','-f',str(out/'manifests.json')],log='server-dry-run.txt')
    command(k+['apply','-f',str(out/'manifests.json')],log='apply.txt')
    command(k+['patch','deployment','iot-gateway','-n','iot-gateway','--type=json','--patch-file',str(out/'controller-patch.json')],log='patch-controller.txt')
    command(k+['rollout','status','deployment/iot-gateway','-n','iot-gateway','--timeout=240s'],log='rollout.txt',timeout=270)
    after=get('get','deployment','iot-gateway','-n','iot-gateway');expected=json.loads(json.dumps(deployment['spec']))
    expected['template']['spec']['containers'][0]['args']=patch[-1]['value'];assert after['spec']==expected
    save('after-gateway.json',after)
    for kind,key in [('sandboxagents','agents'),('workerpools','pools'),('modelconfig','model')]:
        value=get('get',kind,*(['default-model-config'] if key=='model' else []),'-n','iot-agents');save('after-'+key+'.json',value)
        before=tracked[key]
        if key=='model':assert value['spec']==before['spec']
        else:assert {p['metadata']['uid']:p['spec'] for p in value['items']}=={p['metadata']['uid']:p['spec'] for p in before['items']}
    save('ingresses.json',get('get','ingresses','-n','iot-agents'));save('events.json',get('get','events','-n','iot-agents'))
    save('gateway-service.json',get('get','service','iot-gateway','-n','iot-gateway'));save('gateway-pods.json',get('get','pods','-n','iot-gateway'))
    address=get('get','service','iot-gateway','-n','iot-gateway')['spec']['clusterIP'];tls_context=ssl.create_default_context(cafile=str(args.ca))
    expected_leaf=sha256(ssl.PEM_cert_to_DER_cert((out/'server.crt').read_text())).hexdigest();tls_ready={}
    for host,_,_ in ROUTES.values():
        deadline=time.monotonic()+60
        while True:
            try:
                with tls_context.wrap_socket(socket.create_connection((address,443),timeout=5),server_hostname=host) as peer:
                    digest=sha256(peer.getpeercert(binary_form=True)).hexdigest()
                    if digest!=expected_leaf:raise ValueError('The gateway loaded another leaf certificate')
                    tls_ready[host]={'ca_verified':True,'hostname_verified':True,'leaf_sha256':digest};break
            except (OSError,ValueError):
                if time.monotonic()>=deadline:raise RuntimeError('Gateway Pod Ready does not establish accepted TLS routing') from None
                time.sleep(.5)
    save('tls-ready.json',tls_ready)
    source={n:sha256((ROOT/n).read_bytes()).hexdigest() for n in ['scripts/deploy-agent-gateway.py','infra/gateway/agents.py','infra/gateway/assets.json','infra/security/vault_client.py']}
    report={'ready':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_sha256':source,'gateway_uid':after['metadata']['uid'],
            'image':image,'routes':list(ROUTES),'vault_policy':'iot-agent-gateway','vault_secret_version':version,'vault_lease':lease,
            'old_routes_and_model_preserved':True,'authentication_verified':False,'scope':'Private gateway deployment and guarded preservation. Actual TLS/auth/rate/UI requests require the separate native probe.'}
    save('ready.json',report)
    values=[credentials['password'].encode(),htpasswd.encode(),(private/'server.key').read_bytes()]
    for path in out.iterdir():
        if path.is_file():assert not any(v in path.read_bytes() for v in values),'Credential-bearing deployment receipt refused'
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output','private','vault-private','ca']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
