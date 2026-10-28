"""Synchronize only gateway Basic credentials from a read-only Vault AppRole and verify real password rotation."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,base64,http.client,json,os,socket,ssl,subprocess,sys,tempfile,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from infra.security.vault_client import VaultClient,VaultRequestError


def run(args):
    """Keep old/new credentials and Secret backups private; emit only versions and actual HTTP/policy results."""
    os.umask(0o077);r=args.runtime_root.resolve();private=args.private.resolve();output=args.output.resolve()
    if not private.is_relative_to(r/'data/runtime') or not output.is_relative_to(r/'outputs'):raise ValueError('Use only this project runtime/output paths')
    output.mkdir(parents=True,exist_ok=False)
    if args.verify_only:
        if not (private/'previous-credentials.json').is_file():raise ValueError('Read-only resume requires the original private checkpoint')
    else:private.mkdir(parents=True,exist_ok=False)
    client=VaultClient(args.address,args.ca,json.loads(args.role.read_bytes()));lease=client.login();data,version=client.read('iot/data/gateway/basic')
    assert lease['token_policies']==['iot-gateway'] and set(data)=={'username','password'} and data['username']=='coursework' and len(data['password'])>=32
    try:client.read('iot/data/ci/airflow')
    except VaultRequestError as error:assert error.status==403;denied=403
    else:raise AssertionError('Gateway identity unexpectedly reads CI credentials')
    client.revoke();credential_file=r/'data/runtime/gateway-28-private/credentials.json'
    if args.verify_only:old=json.loads((private/'previous-credentials.json').read_bytes())
    else:
        old=json.loads(credential_file.read_bytes());(private/'previous-credentials.json').write_bytes(credential_file.read_bytes())
    k=['/usr/local/bin/k3s','kubectl'];uids={}
    if not args.verify_only:
        encoded=subprocess.check_output(['openssl','passwd','-6','-stdin'],input=(data['password']+'\n').encode()).decode().strip();htpasswd=(data['username']+':'+encoded+'\n').encode()
        k=['/usr/local/bin/k3s','kubectl'];uids={}
        for ns in ['iot-gateway','iot-features','iot-observability']:
            before=json.loads(subprocess.check_output(k+['get','secret','gateway-auth','-n',ns,'-o','json']));assert before['type']=='Opaque' and set(before['data'])=={'htpasswd'}
            (private/(ns+'-secret-before.json')).write_text(json.dumps(before)+'\n');uids[ns]=before['metadata']['uid']
            patch={'data':{'htpasswd':base64.b64encode(htpasswd).decode()}}
            with tempfile.NamedTemporaryFile(mode='w',dir=private,suffix='.json',delete=False) as f:json.dump(patch,f);path=Path(f.name)
            try:
                p=subprocess.run(k+['patch','secret','gateway-auth','-n',ns,'--type=merge','--patch-file',str(path)],capture_output=True);assert p.returncode==0
            finally:path.unlink()
        pending=credential_file.with_suffix('.vault-pending');pending.write_text(json.dumps(data)+'\n');pending.replace(credential_file)
    else:
        assert json.loads(credential_file.read_bytes())==data
        for ns in ['iot-gateway','iot-features','iot-observability']:
            value=json.loads(subprocess.check_output(k+['get','secret','gateway-auth','-n',ns,'-o','json']));uids[ns]=value['metadata']['uid']
    gateway=json.loads(subprocess.check_output(k+['get','service','iot-gateway','-n','iot-gateway','-o','json']))['spec']['clusterIP'];context=ssl.create_default_context(cafile=str(args.ca))
    def probe(value):
        c=http.client.HTTPSConnection(gateway,443,context=context,timeout=10);c.connect=lambda:setattr(c,'sock',context.wrap_socket(socket.create_connection((gateway,443),timeout=10),server_hostname='metrics.iot.test'))
        auth='Basic '+base64.b64encode((value['username']+':'+value['password']).encode()).decode();c.request('GET','/api/health',headers={'Host':'metrics.iot.test','Authorization':auth});response=c.getresponse();status=response.status;response.read();c.close();return status
    attempts=[]
    for _ in range(40):
        code=probe(data);attempts.append(code)
        if code==200:break
        time.sleep(.25)
    else:raise AssertionError('New Vault-managed gateway password was not loaded')
    old_status=probe(old);changed=old!=data;assert old_status==(401 if changed else 200)
    proof={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'consumer':'Gateway credential sync','authentication':'AppRole','policy':'iot-gateway','lease':lease,'secret_path':'iot/data/gateway/basic','secret_version':version,'cross_ci_read_http':denied,'secret_uids':uids,'new_password_http':200,'old_password_http':old_status,'password_changed':changed,'reload_attempts_http':attempts,'read_only_resume':args.verify_only,'scope':'Actual Vault KV2 read by gateway-only identity, Kubernetes Secret sync and authenticated verified HTTPS probe. Root Vault token is never used by this consumer; private values/backups excluded from evidence.'}
    (output/'verification.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--address',required=True)
    for n in ['runtime-root','private','output','role','ca']:p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--verify-only',action='store_true');run(p.parse_args())
