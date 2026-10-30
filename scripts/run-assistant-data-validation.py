"""Ask the observed GPU model three source-grounded data questions and retain every original response, score and token receipt."""
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from pathlib import Path
from urllib.request import Request,ProxyHandler,build_opener
import argparse,base64,http.client,json,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from llm.data_validation import cases,request_body,score_answer
from llm.ab_experiment import reply_text
from llm_observability.contracts import StreamObservation


def run(root,output):
    """Read completed native artifacts, require actual loaded GPU readiness, and evaluate exactly one unedited response per declared case."""
    root=root.resolve();output=output.resolve();os.umask(0o077)
    if not root.is_relative_to('/srv/iot-data') or not output.is_relative_to(root/'outputs'):
        raise ValueError('Use the retained private coursework runtime')
    seal=json.loads((ROOT.parent/'manifest.json').read_bytes())
    if seal['format']!='iot-assistant-data-evaluation-v1':raise ValueError('Unexpected sealed evaluation source')
    for name,pin in seal['files'].items():
        if sha256((ROOT/name).read_bytes()).hexdigest()!=pin:raise ValueError('Evaluation source changed: '+name)
    now=datetime.now(timezone.utc);cpu=json.loads((root/'data/runtime/ci/session.json').read_bytes());gpu=json.loads((root/'data/runtime/ci/gpu-session.json').read_bytes())
    if (datetime.fromisoformat(cpu['auto_stop_utc'])<now+timedelta(minutes=15)
            or datetime.fromisoformat(gpu['last_start_utc'])+timedelta(hours=8)<now+timedelta(minutes=15)):
        raise ValueError('Insufficient approved CPU/GPU session time')
    output.mkdir(exist_ok=False);opener=build_opener(ProxyHandler({}));k=['/usr/local/bin/k3s','kubectl']
    def save(name,value):
        """Preserve finite original synthetic inputs and actual measurements without authentication values."""
        with (output/name).open('x') as f:json.dump(value,f,indent=2,allow_nan=False);f.write('\n')
    def get(*args):
        """Read native Kubernetes resources; secret responses remain only in memory."""
        return json.loads(subprocess.check_output(k+['get',*args,'-o','json'],timeout=30,stderr=subprocess.PIPE))
    def resource_snapshot():
        """Track actual image identities, spec hashes and restarts for the three existing inference components."""
        result={}
        for instance in ['iot-llm-observer','iot-model-control','iot-model-gpu']:
            values=get('pods','-n','iot-llm','-l','app.kubernetes.io/instance='+instance)['items']
            active=[p for p in values if p['status']['phase']=='Running' and not p['metadata'].get('deletionTimestamp')
                    and p['status'].get('containerStatuses') and all(v['ready'] for v in p['status']['containerStatuses'])]
            if len(active)!=1:raise ValueError('Require one actually ready inference Pod for '+instance)
            pod=active[0];result[instance]={'name':pod['metadata']['name'],'uid':pod['metadata']['uid'],
                'spec_sha256':sha256(json.dumps(pod['spec'],sort_keys=True).encode()).hexdigest(),
                'images':[{key:v[key] for key in ['name','image','imageID','restartCount','ready']} for v in pod['status']['containerStatuses']]}
        return result
    before=resource_snapshot();save('pods-before.json',before)
    services={n:get('service',n,'-n','iot-llm') for n in ['iot-llm-observer','iot-model-gpu']}
    if any(v['spec']['type']!='ClusterIP' or v['spec'].get('externalIPs') for v in services.values()):raise ValueError('Require private inference services')
    save('services.json',services);observer=services['iot-llm-observer']['spec']['clusterIP'];address=services['iot-model-gpu']['spec']['clusterIP']
    def fetch(name,path):
        """Keep actual GPU model metadata/counters from the internal service, bounded to two megabytes."""
        with opener.open('http://'+address+':8080'+path,timeout=30) as response:raw=response.read(2000000)
        (output/name).write_bytes(raw);return raw
    models=json.loads(fetch('models.json','/models'));states={v['id']:v['status']['value'] for v in models['data']}
    if states.get('iot-qwen2.5-7b')!='loaded':raise ValueError('The actual 7B model is not loaded')
    metrics_before=fetch('gpu-before.prom','/metrics?model=iot-qwen2.5-7b')
    props=json.loads(fetch('props.json','/props?model=iot-qwen2.5-7b'))
    if props['model_alias']!='iot-qwen2.5-7b':raise ValueError('Wrong model alias')
    inputs={}
    for name,path in [('generator',root/'data/assistant-validation/samples-30b/run/verification.json'),
                      ('offline',root/'data/ci/releases/stream-offline-jenkins-4/outputs/verification.json'),
                      ('online',root/'data/ci/releases/stream-online-jenkins-3/outputs/verification.json')]:
        raw=path.read_bytes();(output/(name+'-input.json')).write_bytes(raw);inputs[name]=json.loads(raw)
    cohort=cases(inputs['generator'],inputs['offline'],inputs['online']);save('cases.json',cohort);save('source.json',seal)
    save('cpu-session.json',cpu);save('gpu-session.json',gpu)
    token=base64.b64decode(get('secret','iot-llm-observer-client','-n','iot-llm')['data']['API_KEY']).decode()
    def ledger():
        """Read the live request ledger through its own installed runtime API, without copying an open SQLite/WAL file."""
        code='from llm_observability.ledger import TelemetryLedger; import json; s=TelemetryLedger("/var/lib/iot-llm-observer/telemetry.sqlite"); print(json.dumps(s.rows())); s.close()'
        return json.loads(subprocess.check_output(k+['exec','-n','iot-llm',before['iot-llm-observer']['name'],'-c','observer','--','python','-B','-c',code],timeout=30,stderr=subprocess.PIPE))
    previous=ledger();old_ids={r['event_id'] for r in previous if r['kind']=='request'};save('ledger-request-ids-before.json',sorted(old_ids));records=[]
    for index,case in enumerate(cohort):
        body=request_body(case);label='case-'+str(index);save(label+'-request.json',body)
        connection=http.client.HTTPConnection(observer,8081,timeout=120);began=time.perf_counter();parser=StreamObservation();chunks=[];wire=[];offset=0
        connection.request('POST','/v1/chat/completions',body=json.dumps(body).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'text/event-stream'})
        response=connection.getresponse();request_id=response.getheader('X-Iot-Request-Id')
        while True:
            piece=response.read1(16384)
            if not piece:break
            elapsed=time.perf_counter()-began;chunks.append({'offset':offset,'bytes':len(piece),'elapsed_seconds':elapsed});wire.append(piece);offset+=len(piece)
            if offset>1048576:raise ValueError('Model response exceeded the bounded transcript budget')
            if response.status==200:parser.feed(piece,elapsed)
        elapsed=time.perf_counter()-began;raw=b''.join(wire);connection.close();(output/(label+'.wire')).write_bytes(raw)
        if response.status!=200 or not request_id:raise ValueError('Inspect the original failed inference response; do not silently retry the case')
        observation=parser.finish(elapsed)
        events=[json.loads(line[5:].strip()) for line in raw.decode().splitlines() if line.startswith('data:') and line[5:].strip()!='[DONE]']
        text=reply_text(events);scored=score_answer(text,case['expected'])
        record={'case_id':case['id'],'request_id':request_id,'http_status':response.status,'elapsed_seconds':elapsed,
                'wire_sha256':sha256(raw).hexdigest(),'chunks':chunks,'observation':observation,'reply':text,'score':scored}
        save(label+'-result.json',record);records.append(record)
    after=resource_snapshot();save('pods-after.json',after)
    if before!=after:raise ValueError('Inference resources changed during the bounded cohort')
    new=[r for r in ledger() if r['kind']=='request' and r['event_id'] not in old_ids];save('ledger-new.json',new)
    if len(new)!=3 or {r['event_id'] for r in new}!={r['request_id'] for r in records}:raise ValueError('Ledger does not identify exactly these three real requests')
    metrics_after=fetch('gpu-after.prom','/metrics?model=iot-qwen2.5-7b')
    def generated(raw):
        """Read the actual provider output-token counter rather than estimate tokens from answer length."""
        values=[float(line.split()[-1]) for line in raw.decode().splitlines() if line.startswith('llamacpp:tokens_predicted_total ')]
        if len(values)!=1:raise ValueError('Require one actual model output counter')
        return values[0]
    delta=generated(metrics_after)-generated(metrics_before)
    usage=sum(r['observation']['usage']['completion_tokens'] for r in records)
    if delta!=usage or any(not r['observation']['complete'] for r in records):raise ValueError('Provider counters or response completion do not match the cohort')
    report={'passed':True,'operational_requests':len(records),'factual_contracts_matched':sum(r['score']['correct'] for r in records),
            'all_factual_contracts_matched':all(r['score']['correct'] for r in records),'actual_gpu_output_tokens':delta,
            'source_commit':seal['commit'],'source_input_commits':{n:v.get('source_commit',v.get('commit')) for n,v in inputs.items()},
            'scope':'Three original direct-model evidence-reading questions through the deployed observer/GPU route. No new MCP tool, continuous-live stream, model retraining or general quality claim. All first outcomes retained.'}
    save('verification.json',report);print(json.dumps(report))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runtime-root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.runtime_root,args.output)
