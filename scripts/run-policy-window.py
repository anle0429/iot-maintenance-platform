"""Measure a bounded synthetic prompt-policy window against the actual observer, ledger, GPU counters and Prometheus."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,build_opener,ProxyHandler
import argparse,base64,http.client,json,os,re,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from llm.policy_window import CASES,MODEL,request_body,summarize
from llm_observability.contracts import StreamObservation


def metric_sum(raw,name,**labels):
    """Sum only the requested native counter series; absent previously unseen categories have zero baseline."""
    total=0.
    for line in raw.decode().splitlines():
        match=re.fullmatch(re.escape(name)+r'(?:\{(.*)\})? ([0-9.eE+\-]+)',line)
        if match:
            actual=dict(re.findall(r'([a-zA-Z_][a-zA-Z_0-9]*)="([^"\\]*)"',match[1] or ''))
            if all(actual.get(k)==v for k,v in labels.items()):total+=float(match[2])
    return total


def run(args):
    """Keep every request and original receipt, requiring unchanged application Pods and no unrelated model traffic."""
    os.umask(0o077);root=args.runtime_root.resolve();out=args.output.resolve()
    assert os.geteuid()==0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root/'outputs')
    seal=json.loads((ROOT.parent/'manifest.json').read_bytes());assert seal['format']=='iot-policy-window-source-v1'
    for name,pin in seal['files'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==pin,name
    session=json.loads((root/'data/runtime/ci/session.json').read_bytes())
    assert (datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc)).total_seconds()>600
    out.mkdir(exist_ok=False);opener=build_opener(ProxyHandler({}));k=['/usr/local/bin/k3s','kubectl']
    def save(name,value):
        """Retain finite actual observations without credentials."""
        (out/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    def get(*query):
        """Read native resource or private Secret data; callers never save a Secret response."""
        return json.loads(subprocess.check_output(k+['get',*query,'-o','json'],timeout=30,stderr=subprocess.PIPE))
    def fetch(name,url):
        """Preserve original private HTTP reads before independently checking their content."""
        with opener.open(url,timeout=30) as response:raw=response.read(4000000);assert response.status==200 and len(raw)<4000000
        (out/name).write_bytes(raw);return raw
    def resources(name):
        """Track the actual observer, adapter and loaded GPU Pod identities and restart counters."""
        result={}
        for instance in ['iot-llm-observer','iot-model-control','iot-model-gpu']:
            values=get('pods','-n','iot-llm','-l','app.kubernetes.io/instance='+instance)['items']
            active=[v for v in values if v['status']['phase']=='Running' and all(c['ready'] for c in v['status'].get('containerStatuses',[]))]
            assert len(active)==1 and active[0]['status'].get('containerStatuses'),instance
            result[instance]=active[0]
        save(name,result);return result
    before=resources('pods-before.json');pod=before['iot-llm-observer']['metadata']['name']
    services={n:get('service',n,'-n','iot-llm') for n in ['iot-llm-observer','iot-model-gpu']}
    assert all(v['spec']['type']=='ClusterIP' for v in services.values());save('services.json',services)
    observer=services['iot-llm-observer']['spec']['clusterIP'];gpu=services['iot-model-gpu']['spec']['clusterIP']
    def ledger(name):
        """Read the live SQLite through its original container API, retaining durable unique request identities."""
        code='from llm_observability.ledger import TelemetryLedger; import json; s=TelemetryLedger("/var/lib/iot-llm-observer/telemetry.sqlite"); print(json.dumps(s.rows())); s.close()'
        raw=subprocess.check_output(k+['exec','-n','iot-llm',pod,'-c','observer','--','python','-B','-c',code],timeout=30,stderr=subprocess.PIPE)
        (out/name).write_bytes(raw);return json.loads(raw)
    previous=ledger('ledger-before.json');old_ids={v['event_id'] for v in previous if v['kind']=='request'}
    before_native=fetch('observer-before.prom','http://'+observer+':8081/metrics')
    model_before=fetch('model-before.prom','http://'+gpu+':8080/metrics?model='+MODEL)
    props=json.loads(fetch('model-props.json','http://'+gpu+':8080/props?model='+MODEL));assert props['model_alias']==MODEL
    save('source.json',seal);save('cases.json',CASES);rows=[]
    token=base64.b64decode(get('secret','iot-llm-observer-client','-n','iot-llm')['data']['API_KEY'])
    started_utc=datetime.now(timezone.utc).isoformat();started=time.perf_counter();save('window-start.json',{'started_utc':started_utc,'paced_seconds':3,'admissions':12})
    for i,case in enumerate(CASES):
        time.sleep(max(0,started+i*3-time.perf_counter()));label='case-'+str(i);body=request_body(i);save(label+'-request.json',body)
        connection=http.client.HTTPConnection(observer,8081,timeout=60);clock=time.perf_counter();parser=StreamObservation();wire=[];chunks=[];offset=0
        connection.request('POST','/v1/chat/completions',body=json.dumps(body).encode(),headers={'Authorization':'Bearer '+token.decode(),'Content-Type':'application/json','Accept':'text/event-stream'})
        response=connection.getresponse();headers_time=time.perf_counter()-clock;identity=response.getheader('X-Iot-Request-Id');assert identity
        while True:
            chunk=response.read1(16384)
            if not chunk:break
            elapsed=time.perf_counter()-clock;chunks.append({'offset':offset,'bytes':len(chunk),'elapsed_seconds':elapsed});wire.append(chunk);offset+=len(chunk);assert offset<1048576
            if response.status==200:parser.feed(chunk,elapsed)
        elapsed=time.perf_counter()-clock;payload=b''.join(wire);connection.close();(out/(label+'.wire')).write_bytes(payload)
        observed=parser.finish(elapsed) if response.status==200 else None
        row={'case_index':i,'case_id':case['id'],'request_id':identity,'request_body':body,'status':response.status,'headers_seconds':headers_time,
            'elapsed_seconds':elapsed,'chunks':chunks,'wire_sha256':sha256(payload).hexdigest(),'response_complete':True,
            'inference_complete':observed['complete'] if observed else False,'usage':observed['usage'] if observed else None,
            'ttft_seconds':observed['ttft_seconds'] if observed else None,'error_code':json.loads(payload)['error']['code'] if response.status!=200 else None}
        rows.append(row);save('requests-in-progress.json',rows);print(json.dumps({'case':i,'status':response.status,'request_id':identity}),flush=True)
    time.sleep(max(0,started+36-time.perf_counter()));seconds=time.perf_counter()-started;finished_utc=datetime.now(timezone.utc).isoformat()
    save('window-end.json',{'finished_utc':finished_utc,'elapsed_seconds':seconds})
    current=ledger('ledger-after.json');new={v['event_id']:v['facts'] for v in current if v['kind']=='request' and v['event_id'] not in old_ids}
    assert set(new)=={r['request_id'] for r in rows}
    for row in rows:
        fact=new[row['request_id']];row['policy_categories']=fact['policy_categories']
        assert row['status']==fact['status'] and row['usage']==fact['usage'] and fact['model']==MODEL and fact['streaming']
        assert fact['duration_seconds']<=row['elapsed_seconds']+.05
        assert (fact['result']=='policy_rejected')==bool(row['policy_categories'])
        assert (fact['ttft_seconds'] is None)==(row['ttft_seconds'] is None)
    save('requests.json',rows);summary=summarize(rows,seconds);save('summary.json',summary)
    after_native=fetch('observer-after.prom','http://'+observer+':8081/metrics');model_after=fetch('model-after.prom','http://'+gpu+':8080/metrics?model='+MODEL)
    delta={outcome:metric_sum(after_native,'iot_llm_policy_checks_total',outcome=outcome)-metric_sum(before_native,'iot_llm_policy_checks_total',outcome=outcome) for outcome in ['allowed','blocked']}
    assert delta=={'allowed':3,'blocked':9}
    category_delta={c:metric_sum(after_native,'iot_llm_policy_hits_total',category=c)-metric_sum(before_native,'iot_llm_policy_hits_total',category=c) for c in summary['category_hits']}
    assert category_delta==summary['category_hits']
    generated=metric_sum(model_after,'llamacpp:tokens_predicted_total')-metric_sum(model_before,'llamacpp:tokens_predicted_total')
    assert generated==summary['provider_reported_tokens']['completion_tokens']>0
    logs=subprocess.check_output(k+['logs','-n','iot-llm',pod,'-c','observer','--timestamps','--since-time='+started_utc],timeout=30,stderr=subprocess.PIPE)
    (out/'observer-timestamped.log').write_bytes(logs);logged=[]
    for line in logs.decode().splitlines():
        stamp,_,text=line.partition(' ')
        try:value=json.loads(text)
        except ValueError:continue
        if value.get('event')=='model_request' and value.get('request_id') in new:logged.append({'timestamp':stamp,**value})
    assert len(logged)==len(new) and {v['request_id'] for v in logged}==set(new);save('request-logs.json',logged)
    after=resources('pods-after.json')
    for name,v in before.items():
        now=after[name];assert now['metadata']['uid']==v['metadata']['uid'] and now['spec']==v['spec']
        assert {c['name']:c['restartCount'] for c in v['status']['containerStatuses']}=={c['name']:c['restartCount'] for c in now['status']['containerStatuses']}
    time.sleep(5);evaluation=time.time();queries={
        'checks':'sum by(outcome)(iot_llm_policy_checks_total{job="iot-llm-observer"})',
        'hits':'sum by(category)(iot_llm_policy_hits_total{job="iot-llm-observer"})',
        'blocked-rate':'sum(rate(iot_llm_policy_checks_total{job="iot-llm-observer",outcome="blocked"}[1m]))*60',
        'category-rate':'sum by(category)(rate(iot_llm_policy_hits_total{job="iot-llm-observer"}[1m]))*60'}
    for name,query in queries.items():
        result=json.loads(fetch('prometheus-'+name+'.json','http://127.0.0.1:19091/api/v1/query?'+urlencode({'query':query,'time':evaluation})))
        assert result['status']=='success' and result['data']['result'],name
        if name in ['checks','hits']:
            field='outcome' if name=='checks' else 'category';native='iot_llm_policy_checks_total' if name=='checks' else 'iot_llm_policy_hits_total'
            for series in result['data']['result']:assert float(series['value'][1])==metric_sum(after_native,native,**{field:series['metric'][field]})
    for name,query in queries.items():
        if name.endswith('rate'):
            result=json.loads(fetch('prometheus-range-'+name+'.json','http://127.0.0.1:19091/api/v1/query_range?'+urlencode({
                'query':query,'start':datetime.fromisoformat(started_utc).timestamp()-10,'end':evaluation,'step':2})))
            assert result['status']=='success' and result['data']['result'],name
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':seal['commit'],'started_utc':started_utc,'finished_utc':finished_utc,
        'summary':summary,'native_policy_counter_delta':delta,'native_category_counter_delta':category_delta,'native_generated_token_delta':generated,
        'unique_ledger_requests':len(new),'timestamped_request_logs':len(logged),'pods_and_restart_counts_unchanged':True,'prometheus_evaluation_unix':evaluation,
        'scope':'Controlled synthetic policy window. Four pattern categories, not universal PII/safety detection; unique blocked prompts differ from category hits. Exact ledger frequencies differ from extrapolated Prometheus rate. No feature lease, physical actuation, maximum-capacity or production-prevalence claim.'}
    save('verification.json',report)
    for path in out.rglob('*'):
        if path.is_file():assert token not in path.read_bytes() and base64.b64encode(token) not in path.read_bytes()
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
