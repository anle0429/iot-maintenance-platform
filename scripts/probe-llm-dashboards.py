"""Reconcile actual LLM dashboards, Loki events, durable facts and private HTTPS gateway readbacks."""
from collections import Counter
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import build_opener,ProxyHandler
import argparse,base64,http.client,json,socket,ssl,subprocess,time


def run(args):
    """Verify recorded facts independently; issue no model, agent or feature request during this administrative check."""
    root=args.runtime_root.resolve();out=args.output.resolve();assert root.is_relative_to('/srv/iot-data') and out.is_relative_to(root/'outputs')
    out.mkdir(exist_ok=False);opener=build_opener(ProxyHandler({}));k=['/usr/local/bin/k3s','kubectl'];stamp=time.time()
    def save(name,value):
        """Retain public observations without copying authorization material."""
        (out/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    def fetch(name,url):
        """Save unchanged private viewer responses with a strict response-size budget."""
        with opener.open(url,timeout=30) as r:raw=r.read(8*1024**2+1);assert r.status==200 and len(raw)<=8*1024**2
        (out/name).write_bytes(raw);return json.loads(raw)
    def get(*query):
        """Read administrative Kubernetes resource identities, never Secret bodies."""
        return json.loads(subprocess.check_output(k+['get',*query,'-o','json'],timeout=30,stderr=subprocess.PIPE))
    pod=get('pods','-n','iot-llm','-l','app.kubernetes.io/instance=iot-llm-observer')['items'][0]
    save('observer-pod.json',pod);uid=pod['metadata']['uid'];name=pod['metadata']['name']
    code='from llm_observability.ledger import TelemetryLedger; import json; s=TelemetryLedger("/var/lib/iot-llm-observer/telemetry.sqlite"); print(json.dumps(s.rows())); s.close()'
    raw=subprocess.check_output(k+['exec','-n','iot-llm',name,'-c','observer','--','python','-B','-c',code],timeout=30,stderr=subprocess.PIPE)
    (out/'ledger.json').write_bytes(raw);ledger=json.loads(raw);requests={v['event_id']:v['facts'] for v in ledger if v['kind']=='request'}
    assert len(requests)>=20
    logs=Path('/var/log/pods')/('iot-llm_'+name+'_'+uid)/'observer';native=[]
    for path in sorted(logs.glob('*.log')):
        data=path.read_bytes();(out/('observer-'+path.name+'.cri')).write_bytes(data)
        for line in data.decode().splitlines():
            ts,stream,flag,message=line.split(' ',3)
            if stream!='stdout' or flag!='F':continue
            try:fact=json.loads(message)
            except ValueError:continue
            if fact.get('event')=='model_request':
                whole,dot,fraction=ts.removesuffix('Z').partition('.')
                ns=int(datetime.fromisoformat(whole).replace(tzinfo=timezone.utc).timestamp())*10**9+int((fraction if dot else '').ljust(9,'0'))
                native.append((str(ns),message))
    assert len(native)==len(requests)
    for _,message in native:
        row=json.loads(message);assert {n:row[n] for n in requests[row['request_id']]}==requests[row['request_id']]
    start=min(int(v[0]) for v in native)-10**9;end=time.time_ns();query='{collection="llm-native-29",event="model_request"}'
    params=urlencode({'query':query,'start':str(start),'end':str(end),'limit':5000,'direction':'forward'})
    loki=fetch('loki-native.json','http://127.0.0.1:13100/loki/api/v1/query_range?'+params)
    rows=[tuple(v) for stream in loki['data']['result'] for v in stream['values']]
    assert Counter(rows)==Counter(native),'Loki differs from original approved CRI events'
    for stream in loki['data']['result']:
        labels=stream['stream'];assert labels['pod_uid']==uid and not {'request_id','trace_id','span_id'}&set(labels)
    panels=[];dashboards={}
    for asset in ['iot-llm-runtime','iot-llm-gpu-host','iot-llm-requests']:
        data=fetch('grafana-'+asset+'.json','http://127.0.0.1:13001/api/dashboards/uid/'+asset);dashboards[asset]=data
        for panel in data['dashboard']['panels']:
            if panel['type'] in {'text','logs'}:continue
            for target in panel['targets']:
                expr=target['expr'].replace('$__range','3h');suffix='/api/v1/query?'+urlencode({'query':expr,'time':str(stamp)})
                label=asset+'-'+str(panel['id']);direct=fetch(label+'-prometheus.json','http://127.0.0.1:19091'+suffix)
                proxy=fetch(label+'-grafana.json','http://127.0.0.1:13001/api/datasources/proxy/uid/iot-host-prometheus'+suffix)
                assert direct['status']==proxy['status']=='success' and direct['data']==proxy['data']
                panels.append({'dashboard':asset,'panel':panel['id'],'expression':expr,'series':len(direct['data']['result'])})
    def total(expr,label):
        """Retain the exact instant vector used for an independent ledger-to-monitoring comparison."""
        result=fetch(label+'.json','http://127.0.0.1:19091/api/v1/query?'+urlencode({'query':expr,'time':str(stamp)}))['data']['result']
        assert len(result)==1;return float(result[0]['value'][1])
    assert total('sum(iot_llm_requests_total)','request-count')==len(requests)
    tokens={kind:sum(v['usage'][field] for v in requests.values() if v['usage']) for kind,field in [('input','prompt_tokens'),('output','completion_tokens'),('total','total_tokens')]}
    for kind,value in tokens.items():assert total('sum(iot_llm_reported_tokens_total{type="'+kind+'"})','tokens-'+kind)==value
    spans=[v['facts'] for v in ledger if v['kind']=='span'];agents=Counter(v['agent'] for v in spans if v['operation']=='agent' and v['agent'] in {'retrieval','drift','coordinator'})
    assert agents=={'retrieval':1,'drift':1,'coordinator':1}
    assert total('sum(iot_agent_invocations_total)','agent-count')==sum(agents.values())
    assert total('sum(up{job=~"iot-agent-mcp|iot-llm-observer|iot-llm-model|iot-llm-gpu-host"})','targets-up')==7
    service=get('service','iot-gateway','-n','iot-gateway');save('gateway-service.json',service);address=service['spec']['clusterIP']
    assert service['spec']['type']=='ClusterIP' and not service['spec'].get('externalIPs')
    credentials=json.loads(args.credentials.read_bytes());auth='Basic '+base64.b64encode((credentials['username']+':'+credentials['password']).encode()).decode()
    context=ssl.create_default_context(cafile=str(args.ca));assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED
    routing=[]
    def request(host,path,mode):
        """Verify the existing lab CA/SNI and authentication at the retained NGINX front door."""
        peer=context.wrap_socket(socket.create_connection((address,443),timeout=15),server_hostname=host)
        certificate=sha256(peer.getpeercert(binary_form=True)).hexdigest();version=peer.version()
        connection=http.client.HTTPSConnection(address,443,context=context,timeout=30);connection.sock=peer
        headers={'Host':host}
        if mode=='correct':headers['Authorization']=auth
        if mode=='incorrect':headers['Authorization']='Basic '+base64.b64encode(b'coursework:deliberately-wrong').decode()
        connection.request('GET',path,headers=headers);response=connection.getresponse();body=response.read(8*1024**2+1);connection.close();assert len(body)<=8*1024**2
        routing.append({'host':host,'path':path,'auth':mode,'status':response.status,'tls_version':version,'leaf_sha256':certificate,'ca_verified':True,'hostname_verified':True,'body_sha256':sha256(body).hexdigest()})
        save('gateway-routing.json',routing);assert response.status==(200 if mode=='correct' else 401)
        return body
    paths=[('metrics.iot.test','/api/dashboards/uid/iot-llm-runtime','runtime'),('logs.iot.test','/api/dashboards/uid/iot-llm-requests','logs'),
        ('traces.iot.test','/api/v3/traces/caee7d7a23dae19af7e76c39a18d8b26','trace')]
    for host,path,label in paths:
        for mode in ['none','incorrect','correct']:
            body=request(host,path,mode);(out/('gateway-'+label+'-'+mode+'.json')).write_bytes(body)
    proxied=json.loads(request('logs.iot.test','/api/datasources/proxy/uid/iot-logs-loki/loki/api/v1/query_range?'+params,'correct'))
    save('gateway-loki.json',proxied);assert Counter(tuple(v) for s in proxied['data']['result'] for v in s['values'])==Counter(native)
    for host,path,label in [('metrics.iot.test','/d/iot-llm-runtime','metrics'),('logs.iot.test','/d/iot-llm-requests','logs'),('traces.iot.test','/trace/caee7d7a23dae19af7e76c39a18d8b26','traces')]:
        (out/('gateway-'+label+'-ui.html')).write_bytes(request(host,path,'correct'))
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'observer_pod_uid':uid,'request_rows':len(requests),'loki_exact_cri_events':len(native),
        'provider_reported_tokens':tokens,'agent_executor_invocations':dict(agents),'grafana_prometheus_panel_matches':len(panels),'targets_up':7,
        'private_https_auth_cases':9,'gateway_log_rows':len(rows),'gateway_ui_htmls':3,
        'scope':'Actual native facts, exact CRI/Loki rows, numerical Prometheus/Grafana parity and retained CA/SNI-authenticated gateway. No browser screenshots, GPU VRAM, load capacity or per-request agent attribution claim.'}
    save('panels.json',panels);save('verification.json',report);save('probe-source.json',{'sha256':sha256(Path(__file__).read_bytes()).hexdigest()});print(json.dumps(report))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output','credentials','ca']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
