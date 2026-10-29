"""Centralize the retained LLM service and gateway credentials in two narrowly authorized Vault AppRoles."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import Request,ProxyHandler,HTTPSHandler,build_opener
from urllib.error import HTTPError
import argparse,base64,json,os,ssl,subprocess,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from infra.security.vault_client import VaultClient,VaultRequestError,_NoRedirect


def run(args):
    """Reuse existing secret bytes, preserve all old policies and record only safe native authorization/readback facts."""
    os.umask(0o077);root=args.runtime_root.resolve();out=args.output.resolve();private=args.private.resolve()
    if not root.is_relative_to('/srv/iot-data') or not out.is_relative_to(root/'outputs') or not private.is_relative_to(root/'data/runtime'):
        raise ValueError('Use only the retained lab workspace and private runtime paths')
    session=json.loads((root/'data/runtime/ci/session.json').read_bytes());remaining=int((datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc)).total_seconds())-120
    if remaining<1800:raise ValueError('Require thirty actual CPU session minutes')
    out.mkdir(parents=True,exist_ok=False);private.mkdir(parents=True,exist_ok=False)
    context=ssl.create_default_context(cafile=str(args.ca));opener=build_opener(ProxyHandler({}),HTTPSHandler(context=context),_NoRedirect())
    token=json.loads((root/'data/runtime/vault-28-private/bootstrap.json').read_bytes())['root_token'];address='https://10.52.0.1:18200'
    def vault(path,body=None,method=None,credential=token):
        """Keep root or revoked tokens in memory and suppress response details on all unexpected failures."""
        request=Request(address+'/v1/'+path,data=json.dumps(body).encode() if body is not None else None,
                        headers={'Content-Type':'application/json','X-Vault-Token':credential},method=method)
        try:
            with opener.open(request,timeout=20) as response:
                raw=response.read(1048577);assert len(raw)<=1048576
                return response.status,json.loads(raw) if raw else {}
        except HTTPError as error:return error.code,{}
    def save(name,value):
        """Persist safe authorization results and source/UID bindings without a token, password or Kubernetes Secret body."""
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    health_status,health=vault('sys/health');assert health_status==200 and health['initialized'] and not health['sealed']
    save('health.json',health)
    k=['/usr/local/bin/k3s','kubectl'];names=['iot-retrieval-mcp-auth','iot-drift-mcp-auth','iot-agent-tool-headers','iot-local-model-client']
    before={name:json.loads(subprocess.check_output(k+['get','secret',name,'-n','iot-agents','-o','json'],stderr=subprocess.PIPE)) for name in names}
    service_data={name:{key:base64.b64decode(value).decode() for key,value in row['data'].items()} for name,row in before.items()}
    code,gateway=vault('iot/data/gateway/basic');assert code==200
    gateway=gateway['data']['data'];assert set(gateway)=={'username','password'} and gateway['username']=='coursework' and len(gateway['password'])>=32
    values={'gateway':gateway,'services':{name:json.dumps(value,sort_keys=True,separators=(',',':')) for name,value in service_data.items()}}
    roles={};private_values=[gateway['password'].encode()]
    for value in service_data.values():private_values.extend(v.encode() for v in value.values())
    for kind,data in values.items():
        path='iot/data/agents/'+kind;code,current=vault(path)
        if code==404:
            code,current=vault(path,{'options':{'cas':0},'data':data});assert code==200
            version=current['data']['version']
        else:
            assert code==200 and current['data']['data']==data,'Preserve a different existing agent secret'
            version=current['data']['metadata']['version']
        role='iot-agent-'+kind;policy=(ROOT/'infra/security'/('vault-agent-'+kind+'.hcl')).read_text()
        code,existing=vault('sys/policies/acl/'+role)
        if code==404:assert vault('sys/policies/acl/'+role,{'policy':policy})[0]==204
        else:assert code==200 and existing['data']['policy']==policy,'Preserve a different existing Vault policy'
        config={'token_policies':[role],'token_no_default_policy':True,'token_ttl':300,'token_max_ttl':900,
                'secret_id_ttl':remaining,'secret_id_num_uses':20,'bind_secret_id':True}
        code,existing=vault('auth/approle/role/'+role)
        if code==404:assert vault('auth/approle/role/'+role,config)[0]==204
        else:
            observed=existing['data'];assert all(observed[key]==config[key] for key in ['token_policies','token_no_default_policy','token_ttl','token_max_ttl','secret_id_num_uses','bind_secret_id'])
        code,observed=vault('auth/approle/role/'+role);assert code==200;save('role-'+kind+'.json',observed)
        code,observed=vault('sys/policies/acl/'+role);assert code==200;save('policy-'+kind+'.json',observed)
        code,identity=vault('auth/approle/role/'+role+'/role-id');assert code==200
        code,secret=vault('auth/approle/role/'+role+'/secret-id',{});assert code==200
        credentials={'role_id':identity['data']['role_id'],'secret_id':secret['data']['secret_id']}
        (private/(kind+'-approle.json')).write_text(json.dumps(credentials)+'\n');private_values.extend(v.encode() for v in credentials.values())
        client=VaultClient(address,args.ca,credentials);lease=client.login();actual,read_version=client.read(path)
        assert lease['token_policies']==[role] and lease['lease_duration']==300 and actual==data and read_version==version
        denied={}
        for other in ['iot/data/agents/'+('services' if kind=='gateway' else 'gateway'),'iot/data/ci/airflow','iot/data/gateway/basic']:
            try:client.read(other)
            except VaultRequestError as error:assert error.status==403;denied[other]=403
            else:raise AssertionError('The agent consumer read another service secret')
        revoked=client._token;client.revoke();revoked_status,_=vault(path,credential=revoked);assert revoked_status==403
        if kind=='gateway':(private/'gateway-credentials.json').write_text(json.dumps(actual)+'\n')
        else:
            synchronized={}
            for name,encoded in actual.items():
                data_map=json.loads(encoded);body={'data':{key:base64.b64encode(value.encode()).decode() for key,value in data_map.items()}}
                p=subprocess.run(k+['patch','secret',name,'-n','iot-agents','--type=merge','--patch-file=/dev/stdin'],input=json.dumps(body).encode(),capture_output=True,timeout=30)
                assert p.returncode==0,'Private Secret synchronization failed'
                now=json.loads(subprocess.check_output(k+['get','secret',name,'-n','iot-agents','-o','json'],stderr=subprocess.PIPE))
                assert now['metadata']['uid']==before[name]['metadata']['uid'] and now['data']==before[name]['data']
                synchronized[name]={'uid':now['metadata']['uid'],'keys':sorted(now['data']),'same_credential_bytes':True}
            save('service-sync.json',{'passed':True,'secrets':synchronized,'credential_rotation':False})
        roles[kind]={'policy':role,'policy_sha256':sha256(policy.encode()).hexdigest(),'path':path,'secret_version':version,
                     'lease':lease,'cross_service_statuses':denied,'revoked_token_status':revoked_status,'readback_exact':True}
        save('consumer-'+kind+'.json',roles[kind])
    source={name:sha256((ROOT/name).read_bytes()).hexdigest() for name in ['scripts/provision-agent-vault.py','infra/security/vault_client.py','infra/security/vault-agent-gateway.hcl','infra/security/vault-agent-services.hcl']}
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_sha256':source,'cluster_id':health['cluster_id'],
            'roles':roles,'existing_service_secret_uids_preserved':True,'credential_values_archived':False,
            'scope':'Actual private TLS Vault KV2, two dedicated AppRoles, denied cross-service access, revoked tokens and exact Kubernetes service-secret readback. No automatic rotation or production HA claim.'}
    save('verification.json',report)
    variants=private_values+[base64.b64encode(v) for v in private_values]+[token.encode()]
    for path in out.iterdir():
        if path.is_file():assert not any(v in path.read_bytes() for v in variants),'Private values found in public receipt'
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output','private','ca']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
