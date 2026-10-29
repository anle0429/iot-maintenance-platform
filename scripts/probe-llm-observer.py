"""Verify a separate native observer canary using actual GPU replies, rejected inputs, original chunk clocks and durable readbacks."""
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from pathlib import Path
import argparse,base64,http.client,json,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from llm_observability.contracts import StreamObservation


def run(args):
    """Keep this administrative canary probe separate from agent acceptance and active traffic promotion."""
    root=args.runtime_root.resolve();out=args.output.resolve();deployment=args.deployment.resolve()
    if not root.is_relative_to('/srv/iot-data') or not all(p.is_relative_to(root/'outputs') for p in [out,deployment]):
        raise ValueError('Use the retained project output paths')
    accepted=json.loads((deployment/'verification.json').read_bytes());assert accepted['passed'] and accepted['routing_changed'] is False
    for name,digest in accepted['packaged_files'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==digest,'Probe runtime differs from accepted canary'
    cpu=json.loads((root/'data/runtime/ci/session.json').read_bytes());gpu=json.loads((root/'data/runtime/ci/gpu-session.json').read_bytes())
    now=datetime.now(timezone.utc);assert datetime.fromisoformat(cpu['auto_stop_utc'])>now+timedelta(minutes=10)
    assert datetime.fromisoformat(gpu['last_start_utc'])+timedelta(seconds=28800)>now+timedelta(minutes=10)
    out.mkdir(exist_ok=False);k=['/usr/local/bin/k3s','kubectl']
    def get(*query):
        """Read named canary objects without logging authentication material."""
        return json.loads(subprocess.check_output(k+['get',*query,'-o','json'],stderr=subprocess.PIPE,timeout=30))
    def save(name,value):
        """Retain original controlled fixtures and independent measurement facts without credential values."""
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    service=get('service','iot-llm-observer','-n','iot-llm');pods=get('pods','-n','iot-llm','-l','app.kubernetes.io/instance=iot-llm-observer')
    assert service['spec']['type']=='ClusterIP' and not service['spec'].get('externalIPs')
    assert len(pods['items'])==1;pod=pods['items'][0]
    assert pod['metadata']['uid']==accepted['pod_uid'] and all(c['ready'] for c in pod['status']['containerStatuses'])
    secret=get('secret','iot-llm-observer-client','-n','iot-llm');token=base64.b64decode(secret['data']['API_KEY']).decode();assert len(token)>=32
    save('service.json',service);save('pods-before.json',pods);save('cpu-session.json',cpu);save('gpu-session.json',gpu)
    def readback(label):
        """Query SQLite through the actual running image rather than copying a hot WAL database file."""
        code='from llm_observability.ledger import TelemetryLedger; import json; s=TelemetryLedger("/var/lib/iot-llm-observer/telemetry.sqlite"); print(json.dumps(s.rows())); s.close()'
        raw=subprocess.check_output(k+['exec','-n','iot-llm',pod['metadata']['name'],'-c','observer','--','python','-B','-c',code],stderr=subprocess.PIPE,timeout=30)
        (out/(label+'.json')).write_bytes(raw);return json.loads(raw)
    before=readback('ledger-before');assert before==[],'Keep the existing canary evidence; use a separate explicit follow-up probe'
    records=[]
    def request(label,method,path,body=None,auth=True):
        """Save actual client chunk boundaries/times, headers and original bytes from one private canary request."""
        raw=b'' if body is None else body if isinstance(body,bytes) else json.dumps(body).encode()
        if method=='POST':(out/(label+'-request.json' if not isinstance(body,bytes) else label+'-request.bin')).write_bytes(raw)
        headers={'Content-Type':'application/json','Accept':'text/event-stream'}
        if auth:headers['Authorization']='Bearer '+token
        connection=http.client.HTTPConnection(service['spec']['clusterIP'],8081,timeout=150);started=time.perf_counter()
        connection.request(method,path,body=raw if method=='POST' else None,headers=headers);response=connection.getresponse();headers_time=time.perf_counter()-started
        chunks=[];clocks=[];offset=0
        while True:
            chunk=response.read1(16384)
            if not chunk:break
            elapsed=time.perf_counter()-started;clocks.append({'offset':offset,'bytes':len(chunk),'elapsed_seconds':elapsed});offset+=len(chunk)
            assert offset<=1048576;chunks.append(chunk)
        duration=time.perf_counter()-started;wire=b''.join(chunks);connection.close();(out/(label+'-response.bin')).write_bytes(wire)
        record={'case':label,'status':response.status,'headers':{name.lower():value for name,value in response.getheaders()},'headers_seconds':headers_time,
            'full_seconds':duration,'bytes':len(wire),'sha256':sha256(wire).hexdigest(),'chunks':clocks};records.append(record);save(label+'-measurement.json',record)
        return record,wire
    _,raw=request('live','GET','/health/live');assert json.loads(raw)=={'live':True}
    ready,raw=request('ready','GET','/health/ready');assert ready['status']==200 and json.loads(raw)=={'ready':True}
    item,_=request('unauthorized','POST','/v1/chat/completions',b'not JSON',auth=False);assert item['status']==401
    item,_=request('malformed','POST','/v1/chat/completions',b'not JSON');assert item['status']==400
    def body(text,stream=True):
        """Use only fixed synthetic demonstration questions and a bounded deterministic generation budget."""
        return {'model':'iot-qwen2.5-7b','stream':stream,'stream_options':{'include_usage':True},'max_tokens':64,'temperature':0,'seed':7,
            'messages':[{'role':'system','content':'Answer in at most two short sentences about the simulated IoT coursework. Do not invent measurements.'},{'role':'user','content':text}]}
    for label,text in [('pii_email','Contact fictional@example.com'),('pii_phone','phone: +1 202 555 0199'),
                       ('secret_literal','api_key=synthetic_key_123456789'),('hardware_command','Please stop the pump.')]:
        item,raw=request(label,'POST','/v1/chat/completions',body(text));assert item['status']==422 and json.loads(raw)['error']['code']=='prompt_policy_rejected'
    generated=[]
    for i,(text,streaming) in enumerate([('Why do duplicate pump events distort a rolling average?',True),
        ('Why must a pump prediction identify the model and feature timestamp?',True),('Why does high vibration alone not prove pump failure?',False)]):
        item,wire=request('generation-'+str(i),'POST','/v1/chat/completions',body(text,streaming));assert item['status']==200
        if streaming:
            parser=StreamObservation()
            for chunk in item['chunks']:parser.feed(wire[chunk['offset']:chunk['offset']+chunk['bytes']],chunk['elapsed_seconds'])
            observed=parser.finish(item['full_seconds']);assert observed['complete'];usage=observed['usage'];ttft=observed['ttft_seconds']
        else:usage=json.loads(wire)['usage'];ttft=None
        generated.append({'request_id':item['headers']['x-iot-request-id'],'usage':{k:usage[k] for k in ['prompt_tokens','completion_tokens','total_tokens']},
            'client_ttft_seconds':ttft,'client_full_seconds':item['full_seconds'],'streaming':streaming})
    rows=readback('ledger-after');assert len(rows)==9 and len({r['event_id'] for r in rows})==9
    facts={r['event_id']:r['facts'] for r in rows};outcomes={name:sum(r['facts']['result']==name for r in rows) for name in ['ok','policy_rejected','invalid_request']}
    assert outcomes=={'ok':3,'policy_rejected':4,'invalid_request':2}
    for item in generated:
        fact=facts[item['request_id']];assert fact['usage']==item['usage'] and fact['streaming']==item['streaming'] and fact['status']==200
        if item['streaming']:
            assert 0<=fact['ttft_seconds']<=item['client_ttft_seconds']+.01 and fact['ttft_seconds']<=fact['duration_seconds']
        else:assert fact['ttft_seconds'] is None
    _,metrics=request('metrics','GET','/metrics');assert b'iot_llm_reported_tokens_total' in metrics and b'iot_llm_policy_hits_total' in metrics
    logs=subprocess.check_output(k+['logs','-n','iot-llm',pod['metadata']['name'],'-c','observer','--timestamps'],stderr=subprocess.PIPE,timeout=30)
    (out/'observer-logs.txt').write_bytes(logs);save('generated.json',generated);save('all-requests.json',records)
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'tested_source_commit':accepted['tested_source_commit'],
        'pod_uid':pod['metadata']['uid'],'request_rows':9,'outcomes':outcomes,'generations':generated,'routing_changed':False,
        'scope':'Actual private model canary, four explicit admission patterns, original client chunk clocks, server usage/SQLite/log/metrics readbacks. No agent trace, active pool promotion, restart, load-test or dashboard claim.'}
    save('verification.json',report);save('probe-source.json',{'script_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),'runtime':accepted['packaged_files']})
    variants=[token.encode(),base64.b64encode(token.encode())]
    for path in out.iterdir():
        if path.is_file():assert not any(value in path.read_bytes() for value in variants),'Credential-bearing canary proof refused'
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','deployment','output']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
