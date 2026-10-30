"""Run a balanced paired comparison of the two real GPU-served artifacts and export only their measured outcomes."""
from collections import Counter
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit,urlencode
from urllib.request import Request,build_opener,ProxyHandler
import argparse,http.client,json,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from llm.ab_experiment import MODELS,CASES,request_body,score_answer,summarize,prometheus_metrics,active_model_pod
from llm_observability.contracts import StreamObservation


def run(args):
    """Keep every original reply/clock, include wrong answers in the denominator and leave the selected agent model unchanged."""
    os.umask(0o077);root=args.runtime_root.resolve();out=args.output.resolve();assert os.geteuid()==0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root/'outputs')
    seal=json.loads((ROOT.parent/'manifest.json').read_bytes());assert seal['format']=='iot-llm-ab-experiment-source-v1'
    for name,pin in seal['files'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==pin,name
    deployed=json.loads(args.deployment.joinpath('verification.json').read_bytes());assert deployed['passed'] and set(deployed['models'])==set(MODELS.values())
    session=json.loads((root/'data/runtime/ci/session.json').read_bytes());assert (datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc)).total_seconds()>900
    out.mkdir(exist_ok=False);opener=build_opener(ProxyHandler({}));k=['/usr/local/bin/k3s','kubectl']
    def save(name,value):
        """Keep finite original measurements and controlled synthetic request content, never authentication material."""
        (out/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    def get(name,*query):
        """Retain actual non-secret native resource identities around the experiment."""
        raw=subprocess.check_output(k+['get',*query,'-o','json'],timeout=30,stderr=subprocess.PIPE);(out/(name+'.json')).write_bytes(raw);return json.loads(raw)
    model=get('model-before','deployment','iot-model-gpu','-n','iot-llm');pods=get('pods-before','pods','-n','iot-llm','-l','app.kubernetes.io/instance=iot-model-gpu')
    running=active_model_pod(pods,model);save('active-pod.json',running)
    primary_config=get('shared-model-before','modelconfig','default-model-config','-n','iot-agents')
    service=get('service','service','iot-model-gpu','-n','iot-llm');assert service['spec']['type']=='ClusterIP';address=service['spec']['clusterIP']
    def fetch(name,path):
        """Capture the actual GPU router's per-model metadata or Prometheus counters."""
        with opener.open('http://'+address+':8080'+path,timeout=30) as response:raw=response.read(2000000);assert response.status==200
        (out/name).write_bytes(raw);return raw
    before_metrics={};after_metrics={}
    for variant,alias in MODELS.items():
        props=json.loads(fetch('props-'+variant+'.json','/props?model='+alias))
        assert props['model_alias']==alias and props['total_slots']==1 and props['default_generation_settings']['n_ctx']==8192
        before_metrics[variant]=fetch('metrics-before-'+variant+'.prom','/metrics?model='+alias)
    rows=[];save('source.json',seal);save('cases.json',CASES);save('deployment-acceptance.json',deployed)
    def complete(phase,variant,repeat,case):
        """Measure original SSE chunk arrival and require provider model identity, usage and an actual terminal frame."""
        label=phase+'-'+str(repeat)+'-'+str(case)+'-'+variant;body=request_body(variant,case);save(label+'-request.json',body)
        parser=StreamObservation();chunks=[];clocks=[];offset=0;connection=http.client.HTTPConnection(address,8080,timeout=120);started=time.perf_counter()
        connection.request('POST','/v1/chat/completions',body=json.dumps(body).encode(),headers={'Content-Type':'application/json','Accept':'text/event-stream'})
        response=connection.getresponse();headers_time=time.perf_counter()-started
        while True:
            chunk=response.read1(16384)
            if not chunk:break
            elapsed=time.perf_counter()-started;clocks.append({'offset':offset,'bytes':len(chunk),'elapsed_seconds':elapsed});offset+=len(chunk);assert offset<=1048576
            chunks.append(chunk)
            if response.status==200:parser.feed(chunk,elapsed)
        elapsed=time.perf_counter()-started;wire=b''.join(chunks);connection.close();(out/(label+'.sse')).write_bytes(wire)
        save(label+'-http.json',{'status':response.status,'headers_seconds':headers_time,'elapsed_seconds':elapsed,'chunks':clocks,'wire_sha256':sha256(wire).hexdigest()})
        assert response.status==200 and wire.endswith(b'\n\n'),label
        observed=parser.finish(elapsed);assert observed['complete'] and observed['usage'] and observed['ttft_seconds'] is not None
        events=[json.loads(line[5:].strip()) for line in wire.decode().splitlines() if line.startswith('data:') and line[5:].strip()!='[DONE]']
        assert {v['model'] for v in events if 'model' in v}=={MODELS[variant]}
        assert {v['system_fingerprint'] for v in events if 'system_fingerprint' in v}=={'b11977-7ac5727d6'}
        usage=next(v['usage'] for v in reversed(events) if v.get('usage'));assert usage['prompt_tokens_details']['cached_tokens']==0
        text=''.join(choice.get('delta',{}).get('content','') for event in events for choice in event.get('choices',[]))
        row={'phase':phase,'variant':variant,'repeat':repeat,'case_index':case,'request_body':body,'model':MODELS[variant],
            'status':response.status,'complete':observed['complete'],'text':text,'usage':observed['usage'],'cached_tokens':0,
            'ttft_seconds':observed['ttft_seconds'],'elapsed_seconds':elapsed,'score':score_answer(case,text),
            'provider_ids':sorted({v['id'] for v in events if v.get('id')}),'wire_sha256':sha256(wire).hexdigest()}
        rows.append(row);save('requests.json',rows)
        print(json.dumps({'phase':phase,'variant':variant,'case_index':case,'repeat':repeat,'contract_correct':row['score']['correct'],'seconds':elapsed}),flush=True)
    for case in range(4):
        for variant in MODELS:complete('warmup',variant,-1,case)
    for repeat in range(3):
        for case in range(4):
            for variant in (['small','large'] if (repeat+case)%2==0 else ['large','small']):complete('measured',variant,repeat,case)
    summary=summarize(rows);save('summary.json',summary);metrics=prometheus_metrics(summary);(out/'experiment.prom').write_bytes(metrics)
    request=Request('http://127.0.0.1:19092/metrics/job/iot-llm-ab/run/'+out.name,data=metrics,method='PUT',headers={'Content-Type':'text/plain; version=0.0.4'})
    with opener.open(request,timeout=15) as response:save('pushgateway-write.json',{'status':response.status});assert response.status==200
    for variant,alias in MODELS.items():after_metrics[variant]=fetch('metrics-after-'+variant+'.prom','/metrics?model='+alias)
    current=get('model-after','deployment','iot-model-gpu','-n','iot-llm');now_pods=get('pods-after','pods','-n','iot-llm','-l','app.kubernetes.io/instance=iot-model-gpu')
    assert model['metadata']['uid']==current['metadata']['uid'] and model['spec']==current['spec']
    after_pod=active_model_pod(now_pods,current);assert after_pod['metadata']['uid']==running['metadata']['uid']
    assert {v['name']:v['restartCount'] for v in after_pod['status']['containerStatuses']}=={v['name']:v['restartCount'] for v in running['status']['containerStatuses']}
    assert get('shared-model-after','modelconfig','default-model-config','-n','iot-agents')['spec']==primary_config['spec']
    def predicted(raw):
        """Read the actual native generated-token counter, independently of the client usage parser."""
        lines=[v for v in raw.decode().splitlines() if v.startswith('llamacpp:tokens_predicted_total ')];assert len(lines)==1;return float(lines[0].split()[-1])
    counter_deltas={variant:predicted(after_metrics[variant])-predicted(before_metrics[variant]) for variant in MODELS}
    for variant in MODELS:assert counter_deltas[variant]==sum(r['usage']['completion_tokens'] for r in rows if r['variant']==variant)
    query='iot_llm_ab_measured_requests{run="'+out.name+'"}'
    for attempt in range(15):
        with opener.open('http://127.0.0.1:19091/api/v1/query?'+urlencode({'query':query}),timeout=15) as response:raw=response.read()
        (out/('prometheus-readback-'+str(attempt)+'.json')).write_bytes(raw);values=json.loads(raw)['data']['result']
        if len(values)==2 and all(float(v['value'][1])==12 for v in values):break
        time.sleep(2)
    else:raise ValueError('Actual experiment metrics were not scraped')
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':seal['commit'],'measured_requests':24,'warmup_requests':8,
        'distinct_contract_cases':4,'repeats_per_case':3,'summary':summary,'native_generated_token_counter_deltas':counter_deltas,
        'primary_model_config_unchanged':True,'model_pod_uid':running['metadata']['uid'],
        'retained_terminal_pods':[{'uid':v['metadata']['uid'],'phase':v['status']['phase'],'reason':v['status'].get('reason')} for v in pods['items'] if v['status']['phase'] in {'Failed','Succeeded'}],
        'scope':'Two genuinely different pinned model artifacts, same GPU/image/context/threads, four synthetic contract questions repeated three times, balanced paired order, no prompt caching. Contract-case results are not real-world predictive accuracy or a general model-quality ranking. No automatic model promotion.'}
    save('verification.json',report);print(json.dumps(report),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output','deployment']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
