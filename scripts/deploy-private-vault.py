"""Start checksum-pinned non-dev Vault with Raft and private TLS, then provision two least-privilege AppRoles."""
from pathlib import Path
from datetime import datetime,timezone
from hashlib import sha256
from urllib.request import Request,build_opener,HTTPSHandler,HTTPRedirectHandler
from urllib.error import HTTPError
import argparse,json,os,socket,ssl,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


class NoRedirect(HTTPRedirectHandler):
    """Never send the bootstrap root token through a redirect."""
    def redirect_request(self,*args):return None


def run(args):
    """Keep init/unseal/root/AppRole values in private files, and emit only non-secret native receipts."""
    os.umask(0o077);r=args.runtime_root.resolve();session=json.loads((r/'data/runtime/ci/session.json').read_bytes());remaining=int((datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc)).total_seconds())
    if remaining<1800 or not r.is_relative_to('/srv/iot-data') or not Path('/srv/iot-fast').is_mount():raise ValueError('Use existing VM disks with30minutes in the approved session')
    pins=json.loads((ROOT/'infra/security/vault-assets.json').read_bytes());binary=r/'data/runtime/vault-assets-28/vault'
    if sha256(binary.read_bytes()).hexdigest()!=pins['binary_sha256']:raise ValueError('Official Vault binary checksum differs')
    for port in [18200,18201]:
        with socket.socket() as s:s.bind(('10.52.0.1',port))
    out=args.output;private=args.private;server=args.server;state=args.state
    for path in [out,private,server,state]:path.mkdir(parents=True,exist_ok=False)
    for path in [server,state]:os.chown(path,65534,65534);path.chmod(0o700)
    def save(n,v):(out/n).write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')
    def command(values,stdin=None,log=None):
        p=subprocess.run(values,input=stdin,capture_output=True,timeout=60)
        if log:(out/log).write_bytes(p.stdout+p.stderr)
        if p.returncode:raise RuntimeError('Vault command failed; retained log '+str(log))
        return p.stdout
    address=json.loads(command(['ip','-j','-4','address','show','cni0']))[0]['addr_info'][0]['local'];assert address=='10.52.0.1'
    # Keys are private to the server user; the existing private lab CA is used only by this bootstrap signer.
    ca=r/'outputs/gateway-28/deploy/ca.crt';cakey=r/'data/runtime/gateway-28-private/ca.key';(out/'ca.crt').write_bytes(ca.read_bytes())
    command(['openssl','req','-new','-newkey','rsa:3072','-nodes','-keyout',str(server/'server.key'),'-out',str(private/'server.csr'),'-subj','/CN=vault.iot.test'],log='tls-request.txt')
    (private/'extensions.cnf').write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:vault.iot.test,IP:'+address+'\n')
    command(['openssl','x509','-req','-in',str(private/'server.csr'),'-CA',str(ca),'-CAkey',str(cakey),'-set_serial','0x'+os.urandom(16).hex(),'-out',str(server/'server.crt'),'-days','2','-extfile',str(private/'extensions.cnf')],log='tls-sign.txt')
    (out/'server.crt').write_bytes((server/'server.crt').read_bytes())
    config='ui = true\ndisable_mlock = true\napi_addr = "https://'+address+':18200"\ncluster_addr = "https://'+address+':18201"\nstorage "raft" {\n  path = "'+str(state/'raft')+'"\n  node_id = "iot-vault-single"\n}\nlistener "tcp" {\n  address = "'+address+':18200"\n  cluster_address = "'+address+':18201"\n  tls_cert_file = "'+str(server/'server.crt')+'"\n  tls_key_file = "'+str(server/'server.key')+'"\n  tls_min_version = "tls12"\n}\n'
    (server/'vault.hcl').write_text(config);(out/'vault.hcl').write_text(config)
    for p in server.iterdir():os.chown(p,65534,65534);p.chmod(0o400)
    unit='[Unit]\nDescription=IoT private non-dev Vault2.2.1 Raft\nAfter=network-online.target\n[Service]\nUser=nobody\nGroup=nogroup\nExecStart='+str(binary)+' server -config='+str(server/'vault.hcl')+'\nNoNewPrivileges=true\nProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\nReadWritePaths='+str(state)+'\nMemoryMax=768M\nMemorySwapMax=0\nCPUQuota=100%\nRestart=on-failure\nRestartSec=5\n'
    unitpath=Path('/run/systemd/system/iot-vault-28.service');assert not unitpath.exists();unitpath.write_text(unit);(out/'vault.service').write_text(unit)
    command(['systemctl','daemon-reload']);command(['systemctl','start','iot-vault-28'])
    context=ssl.create_default_context(cafile=str(ca));opener=build_opener(HTTPSHandler(context=context),NoRedirect());base='https://'+address+':18200/v1/'
    def api(path,body=None,token=None,method=None,allowed=()):
        headers={'Content-Type':'application/json'}
        if token:headers['X-Vault-Token']=token
        request=Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
        try:
            with opener.open(request,timeout=15) as response:
                raw=response.read(1_048_577);assert len(raw)<=1_048_576;return json.loads(raw) if raw else {}
        except HTTPError as e:
            if e.code in allowed:return json.loads(e.read())
            raise RuntimeError('Private Vault API HTTP '+str(e.code)+' at '+path) from None
    for _ in range(30):
        try:health=api('sys/health',allowed=[501,503]);break
        except (OSError,RuntimeError):time.sleep(1)
    else:raise RuntimeError('Vault did not start; inspect journal without changing original output')
    save('health-before-init.json',health);assert health['initialized'] is False
    bootstrap=api('sys/init',{'secret_shares':1,'secret_threshold':1});(private/'bootstrap.json').write_text(json.dumps(bootstrap)+'\n')
    unsealed=api('sys/unseal',{'key':bootstrap['keys_base64'][0]});assert unsealed['sealed'] is False;save('unsealed.json',unsealed);token=bootstrap['root_token'];bootstrap=None
    for _ in range(30):
        health=api('sys/health',allowed=[503]);
        if health['initialized'] and not health['sealed'] and not health['standby']:break
        time.sleep(1)
    else:raise RuntimeError('Vault did not become active')
    # Audit defaults HMAC sensitive fields; raw audit stays on the existing HDD.
    api('sys/audit/file',{'type':'file','options':{'file_path':str(state/'audit.jsonl'),'log_raw':'false'}},token)
    api('sys/mounts/iot',{'type':'kv','options':{'version':'2'}},token)
    gateway=json.loads((r/'data/runtime/gateway-28-private/credentials.json').read_bytes());airflow=json.loads((r/'data/runtime/secrets/orchestration-users.json').read_bytes())['airflow']
    versions={}
    for path,data in [('gateway/basic',gateway),('ci/airflow',{'username':'learner','password':airflow,'url':'http://127.0.0.1:18480'})]:versions[path]=api('iot/data/'+path,{'options':{'cas':0},'data':data},token)['data']['version']
    gateway=airflow=None;api('sys/auth/approle',{'type':'approle'},token);roles={}
    for name,policyfile in [('ci','vault-ci.hcl'),('gateway','vault-gateway.hcl')]:
        policy=(ROOT/'infra/security'/policyfile).read_text();api('sys/policies/acl/iot-'+name,{'policy':policy},token)
        config={'token_policies':['iot-'+name],'token_no_default_policy':True,'token_ttl':300,'token_max_ttl':900,'secret_id_ttl':remaining,'secret_id_num_uses':20,'bind_secret_id':True}
        api('auth/approle/role/iot-'+name,config,token);role=api('auth/approle/role/iot-'+name+'/role-id',token=token)['data']['role_id'];secret=api('auth/approle/role/iot-'+name+'/secret-id',{},token)['data']['secret_id']
        (private/(name+'-approle.json')).write_text(json.dumps({'role_id':role,'secret_id':secret})+'\n');roles[name]={'policy':'iot-'+name,**config}
    save('health.json',api('sys/health'));save('roles.json',roles);save('secret-versions.json',versions)
    save('source.json',{'files':{n:sha256((ROOT/n).read_bytes()).hexdigest() for n in ['scripts/deploy-private-vault.py','infra/security/vault-assets.json','infra/security/vault_client.py','infra/security/vault-ci.hcl','infra/security/vault-gateway.hcl']}})
    save('ready.json',{'ready':True,'utc':datetime.now(timezone.utc).isoformat(),'address':'https://'+address+':18200','storage':'raft','dev_mode':False,'shares':1,'threshold':1,'roles':list(roles),'token_ttl_seconds':300,'versions':versions,'scope':'One existing VM/private CNI listener/768MiB/no swap, non-dev persistent Raft and TLS. Single operator lab bootstrap retained0600 outside Git; not HA or production unseal separation. Consumer tests follow separately.'})
    print((out/'ready.json').read_text())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['runtime-root','output','private','server','state']:p.add_argument('--'+n,type=Path,required=True)
    run(p.parse_args())
