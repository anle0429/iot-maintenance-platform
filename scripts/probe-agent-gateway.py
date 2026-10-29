"""Exercise actual private HTTPS agent/catalog routes, authentication failures, header binding and burst rejection."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
import argparse,base64,http.client,json,re,socket,ssl,subprocess,time


def run(args):
    """Keep credentials private while preserving original response bodies and CA/SNI-verified transport facts."""
    out=args.output.resolve();root=args.runtime_root.resolve()
    if not out.is_relative_to(root/'outputs'):raise ValueError('Preserve original probes under this lab output directory')
    out.mkdir(parents=True,exist_ok=False);k=['/usr/local/bin/k3s','kubectl']
    gateway=json.loads(subprocess.check_output(k+['get','service','iot-gateway','-n','iot-gateway','-o','json']))
    assert gateway['spec']['type']=='ClusterIP' and not gateway['spec'].get('externalIPs');address=gateway['spec']['clusterIP']
    credentials=json.loads(args.credentials.read_bytes());authorization='Basic '+base64.b64encode((credentials['username']+':'+credentials['password']).encode()).decode()
    context=ssl.create_default_context(cafile=str(args.deployment/'ca.crt'))
    def save(name,value):
        """Write safe response bodies and observed native transport facts in strict JSON."""
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    def request(host,path,auth=authorization,tls=True,extra=None):
        """Verify the private peer's CA and requested hostname on each bounded request without following redirects."""
        started=time.monotonic();connection=http.client.HTTPConnection(address,80,timeout=20)
        if tls:
            connection=http.client.HTTPSConnection(address,443,context=context,timeout=20)
            connection.connect=lambda:setattr(connection,'sock',context.wrap_socket(socket.create_connection((address,443),timeout=20),server_hostname=host))
        connection.connect();transport={'ca_verified':True,'hostname_verified':True,'version':connection.sock.version(),
                                      'leaf_sha256':sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()} if tls else None
        headers={'Host':host,**({'Authorization':auth} if auth else {}),**(extra or {})};connection.request('GET',path,headers=headers)
        response=connection.getresponse();body=response.read(4194305);assert len(body)<=4194304
        selected={name:value for name,value in response.getheaders() if name.lower() in ['content-type','location','www-authenticate','server','date']}
        result={'host':host,'path':path,'auth':'correct' if auth==authorization else 'none' if not auth else 'incorrect',
                'status':response.status,'headers':selected,'body':body.decode(),'body_sha256':sha256(body).hexdigest(),
                'tls':transport,'elapsed_seconds':time.monotonic()-started}
        connection.close();return result
    rows=[];pages={}
    for role,host,api in [('agents','agents.iot.test','/api/me'),('registry','registry.iot.test','/v0/agents')]:
        for path in ['/',api]:
            for mode,header in [('none',None),('incorrect','Basic '+base64.b64encode(b'coursework:incorrect-native-test').decode()),('correct',authorization)]:
                row=request(host,path,header);rows.append(row);save('routing-auth.json',rows)
                assert row['status']==(200 if mode=='correct' else 401),(role,path,mode,row['status'])
                if mode=='correct' and path=='/':pages[role]=row['body']
        redirect=request(host,'/',None,False);rows.append(redirect);save('routing-auth.json',rows)
        target=urlsplit(redirect['headers'].get('Location',''));assert redirect['status']==308 and target.scheme=='https' and target.hostname==host
        scripts=re.findall(r'<script[^>]+src=["\']([^"\']+)["\']',pages[role])
        internal=[value for value in scripts if value.startswith('/') and not value.startswith('//')]
        assert internal,'A real UI must load a script through the same protected host'
        asset=request(host,internal[0]);save(role+'-javascript.json',asset);assert asset['status']==200 and asset['body']
    me=request('agents.iot.test','/api/me',extra={'X-User-Id':'untrusted-client-identity','X-Forwarded-User':'untrusted-client-identity'})
    save('identity-override.json',me);assert me['status']==200
    identity=json.loads(me['body']);assert identity['sub']=='admin@kagent.dev','Map the authenticated operator to the pinned UI lab principal'
    entries={}
    for role in ['retrieval','drift','coordinator']:
        row=request('registry.iot.test','/v0/agents/iot-'+role+'/0.2.2');save('catalog-'+role+'.json',row);assert row['status']==200
        entries[role]=json.loads(row['body'])
    old=[]
    for host,path in [('metrics.iot.test','/api/health'),('logs.iot.test','/api/dashboards/uid/iot-linux-logs'),
                      ('traces.iot.test','/api/v3/services'),('features.iot.test','/health/live')]:
        row=request(host,path);old.append(row);save('old-routes.json',old);assert row['status']==200
    negatives=[]
    for name,ctx,host in [('untrusted-ca',ssl.create_default_context(),'agents.iot.test'),('wrong-hostname',context,'incorrect.iot.test')]:
        try:
            with ctx.wrap_socket(socket.create_connection((address,443),timeout=10),server_hostname=host):pass
        except ssl.SSLCertVerificationError as error:negatives.append({'case':name,'rejected':True,'verify_code':error.verify_code,'verify_message':error.verify_message})
        except ssl.SSLError as error:
            if name!='wrong-hostname' or error.reason!='TLSV1_UNRECOGNIZED_NAME':raise
            negatives.append({'case':name,'rejected':True,'reason':error.reason,'stage':'server rejected unknown SNI before presenting a certificate'})
        else:raise AssertionError('Invalid TLS identity unexpectedly accepted')
    save('tls-negative.json',negatives)
    time.sleep(4);started=datetime.now(timezone.utc).isoformat();begin=time.monotonic()
    with ThreadPoolExecutor(max_workers=64) as pool:burst=list(pool.map(lambda _:request('agents.iot.test','/api/me'),range(320)))
    duration=time.monotonic()-begin;counts={str(code):sum(r['status']==code for r in burst) for code in {r['status'] for r in burst}}
    save('burst.json',{'started_utc':started,'duration_seconds':duration,'responses':burst,'counts':counts})
    assert set(counts)=={'200','429'} and all(counts.values()),counts
    time.sleep(4);recovered=request('agents.iot.test','/api/me');save('recovered.json',recovered);assert recovered['status']==200
    for filename,command in [('nginx-config.txt',['exec','deployment/iot-gateway','-n','iot-gateway','--','nginx','-T']),
                             ('controller.log',['logs','deployment/iot-gateway','-n','iot-gateway','--tail=250']),
                             ('ingresses.json',['get','ingress','-n','iot-agents','-o','json']),('events.json',['get','events','-n','iot-agents','-o','json'])]:
        result=subprocess.run(k+command,capture_output=True,timeout=30);(out/filename).write_bytes(result.stdout+result.stderr);assert result.returncode==0
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),
        'gateway_ip':address,'new_hosts':['agents.iot.test','registry.iot.test'],'unauthorized_401':8,'redirects_308':2,
        'authenticated_ui_pages':2,'authenticated_javascript_assets':2,'native_catalog_versions':3,'frontend_identity':identity['sub'],
        'spoofed_identity_header_ignored':True,'account_model':'One authenticated operator mapped to the existing internal UI lab principal',
        'preserved_old_routes':4,'tls_negative_cases':2,'burst_requests':320,'burst_statuses':counts,
        'burst_duration_seconds':duration,'recovery_http':200,'scope':'Actual private NGINX TLS/Basic/rate controls and working frontend assets/API/catalog. Single operator lab; not public DNS, browser CA trust, multi-tenant controller authorization or a new LLM conversation.'}
    save('verification.json',report)
    secret_values=[credentials['password'].encode(),authorization.encode(),base64.b64encode((credentials['username']+':'+credentials['password']).encode())]
    for path in out.iterdir():
        if path.is_file():assert not any(v in path.read_bytes() for v in secret_values),'Credential-bearing native probe refused'
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','deployment','credentials','output']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
