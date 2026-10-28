"""Verify native TLS, auth, routing and bounded burst rejection on the private NGINX gateway."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
import argparse,base64,http.client,json,socket,ssl,subprocess,time
from urllib.parse import urlsplit

HOSTS={'metrics':'metrics.iot.test','logs':'logs.iot.test','traces':'traces.iot.test','features':'features.iot.test'}


def run(args):
    """Use explicit CA/hostname verification, keep auth private, and save actual responses before asserting success."""
    out=args.output;out.mkdir(parents=True,exist_ok=False)
    def save(name,value):'Persist strict JSON evidence for the gateway check.';(out/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    k=['/usr/local/bin/k3s','kubectl'];get=lambda *a:json.loads(subprocess.check_output(k+list(a)+['-o','json']))
    service=get('get','service','iot-gateway','-n','iot-gateway');assert service['spec']['type']=='ClusterIP' and not service['spec'].get('externalIPs')
    address=service['spec']['clusterIP'];credentials=json.loads(args.credentials.read_bytes())
    auth='Basic '+base64.b64encode((credentials['username']+':'+credentials['password']).encode()).decode();credentials=None
    context=ssl.create_default_context(cafile=str(args.deployment/'ca.crt'));assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED
    responses=[]
    def request(host,path,authorization=auth,tls=True):
        'Call one reviewed virtual host with CA/SNI validation and bounded timeout.'
        started=time.monotonic();c=http.client.HTTPConnection(address,80,timeout=15)
        if tls:
            c=http.client.HTTPSConnection(address,443,context=context,timeout=15)
            c.connect=lambda:setattr(c,'sock',context.wrap_socket(socket.create_connection((address,443),timeout=15),server_hostname=host))
        c.connect();tls_version=c.sock.version() if tls else None
        c.request('GET',path,headers={'Host':host,**({'Authorization':authorization} if authorization is not None else {})})
        r=c.getresponse();body=r.read(4_194_305);assert len(body)<=4_194_304
        result={'host':host,'path':path,'auth':'correct' if authorization==auth else 'none' if authorization is None else 'incorrect','status':r.status,'headers':dict(r.getheaders()),'body':body.decode('utf-8',errors='strict'),'elapsed_seconds':time.monotonic()-started}
        if tls:result['tls']={'version':tls_version,'ca_verified':True,'hostname_verified':True}
        c.close();return result
    paths={'metrics':'/api/dashboards/uid/iot-api-observability','logs':'/api/dashboards/uid/iot-linux-logs','traces':'/api/v3/services','features':'/health/ready'}
    for name,host in HOSTS.items():
        for mode,header in [('none',None),('incorrect','Basic '+base64.b64encode(b'coursework:deliberately-wrong').decode()),('correct',auth)]:
            row=request(host,paths[name],header);responses.append(row);save('routing-auth.json',responses)
            assert row['status']==(200 if mode=='correct' else 401),(name,mode,row['status'])
            time.sleep(.25)
        row=request(host,paths[name],None,False);responses.append(row);save('routing-auth.json',responses)
        target=urlsplit(row['headers'].get('Location',''));assert row['status']==308 and target.scheme=='https' and target.hostname==host and (target.port or 443)==443 and target.path==paths[name] and not target.query and target.username is None
    assert json.loads(next(r['body'] for r in responses if r['host']==HOSTS['metrics'] and r['auth']=='correct'))['dashboard']['uid']=='iot-api-observability'
    assert json.loads(next(r['body'] for r in responses if r['host']==HOSTS['logs'] and r['auth']=='correct'))['dashboard']['uid']=='iot-linux-logs'
    assert set(json.loads(next(r['body'] for r in responses if r['host']==HOSTS['traces'] and r['auth']=='correct'))['services'])>={'iot-feature-api','iot-predictor-api','iot-drift-api'}
    logdata=request(HOSTS['logs'],'/api/datasources/proxy/uid/iot-logs-loki/loki/api/v1/labels');save('logs-native-query.json',logdata);assert logdata['status']==200 and json.loads(logdata['body'])['status']=='success' and json.loads(logdata['body'])['data']
    negative=[]
    for name,ctx,hostname in [('untrusted-ca',ssl.create_default_context(),HOSTS['features']),('wrong-hostname',context,'wrong.iot.test')]:
        try:
            with ctx.wrap_socket(socket.create_connection((address,443),timeout=10),server_hostname=hostname):pass
        except ssl.SSLError as error:negative.append({'case':name,'rejected':True,'type':type(error).__name__,'reason':error.reason,'verify_code':getattr(error,'verify_code',None),'verify_message':getattr(error,'verify_message',None)})
        else:raise AssertionError('Invalid TLS identity unexpectedly accepted')
    save('tls-negative.json',negative)
    feature_context=json.loads((args.feature_state/'context.json').read_bytes());machine=next(n for n,v in feature_context['machines'].items() if v['status']=='operating')
    path='/v1/machines/'+machine+'/features';row=request(HOSTS['features'],path);save('feature-http.json',row);assert row['status']==200
    backend=get('get','service','iot-feature-api','-n','iot-features')['spec']['clusterIP'];c=http.client.HTTPConnection(backend,80,timeout=10);c.request('GET',path);r=c.getresponse();direct={'status':r.status,'body':r.read().decode()};c.close();save('feature-direct.json',direct)
    assert direct['status']==200 and json.loads(direct['body'])==json.loads(row['body'])
    time.sleep(2)
    began=datetime.now(timezone.utc).isoformat();start=time.monotonic()
    with ThreadPoolExecutor(max_workers=16) as pool:burst=list(pool.map(lambda _:request(HOSTS['features'],path),range(32)))
    duration=time.monotonic()-start;save('burst.json',{'started_utc':began,'duration_seconds':duration,'responses':burst})
    counts={str(code):sum(r['status']==code for r in burst) for code in set(r['status'] for r in burst)}
    assert set(counts)<={'200','429'} and int(counts.get('200',0))>0 and int(counts.get('429',0))>0,counts
    time.sleep(2);recovered=request(HOSTS['features'],path);save('recovered.json',recovered);assert recovered['status']==200
    (out/'nginx-config.txt').write_bytes(subprocess.check_output(k+['exec','deployment/iot-gateway','-n','iot-gateway','--','nginx','-T'],stderr=subprocess.STDOUT))
    (out/'controller.log').write_bytes(subprocess.check_output(k+['logs','deployment/iot-gateway','-n','iot-gateway','--tail=250']))
    save('pods.json',get('get','pods','-n','iot-gateway'));save('service.json',service)
    save('verification.json',{'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'routes':4,'unauthorized_401':8,'https_redirects_308':4,'tls_negative_cases':2,'ca_required':True,'hostname_required':True,'burst_requests':32,'burst_statuses':counts,'burst_duration_seconds':duration,'recovery_status':200,'exact_feature_backend_match':True,'machine_id':machine,'scope':'Private .test DNS names resolved to existing ClusterIP with explicit SNI/CA. No public DNS, browser trust bypass, Internet gateway, max-throughput or cluster-wide network isolation claim.'})
    print((out/'verification.json').read_text())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['deployment','credentials','output','feature-state']:p.add_argument('--'+n,type=Path,required=True)
    run(p.parse_args())
