"""Send a bounded real API workload and compare per-Pod SDK counter deltas with an independent HTTP ledger."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
from collections import Counter
import argparse,json,time,urllib.request,urllib.error
from hashlib import sha256
from prometheus_client.parser import text_string_to_metric_families


def run(plan,rollout,output,duration=60):
    """Keep health/scrapes outside business counts; one drift window may be replayed without adding logical rows."""
    if not 30<=duration<=90:raise ValueError('Use a bounded30–90second observation window')
    output.mkdir(exist_ok=False);expected=Counter();rows=[];examples={}
    def save(name,value):(output/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    def scrape(stage):
        counts=Counter()
        for kind,targets in plan['targets'].items():
            for target in targets:
                with urllib.request.urlopen(target['url']+'/telemetry/metrics',timeout=10) as response:
                    assert response.status==200;raw=response.read(2_097_153);assert len(raw)<=2_097_152
                (output/(stage+'-'+kind+'-'+target['pod_uid']+'.prom')).write_bytes(raw)
                for family in text_string_to_metric_families(raw.decode()):
                    for sample in family.samples:
                        if sample.name=='http_server_duration_milliseconds_count':
                            key=(kind,sample.labels['http_method'],sample.labels['http_status_code'],sample.labels.get('http_target',''))
                            assert sample.labels['service_name']=='iot-'+kind+'-api';counts[key]+=sample.value
        return counts
    before=scrape('before');save('counter-before.json',[{'key':k,'value':v} for k,v in sorted(before.items())])
    cohort=json.loads(Path(rollout['cohort_path']).read_bytes());operating=[r for r in cohort if r['prediction_status']==200]
    stopped=next(r['machine_id'] for r in cohort if r['prediction_status']==409);first=operating[0]
    logical=datetime.fromisoformat(first['features']['as_of'])
    window={'reference_id':rollout['drift_reference_id'],'window_id':'api-observability-28b-v4',
        'start':(logical-timedelta(hours=1)).isoformat(),'end':(logical+timedelta(minutes=1)).isoformat(),
        'observations':[{'id':r['machine_id'],'event_timestamp':r['features']['feature_event_ts'],'available_at':r['features']['as_of'],'features':r['features']['features']} for r in operating[:32]]}
    save('drift-window.json',window)
    with (output/'requests.jsonl').open('x') as journal:
        def request(kind,method,path,route,wanted,body=None,headers=None):
            started=datetime.now(timezone.utc).isoformat();timer=time.perf_counter()
            call=urllib.request.Request(rollout['urls'][kind]+path,method=method,data=json.dumps(body).encode() if body is not None else None,
                headers={'Content-Type':'application/json',**(headers or {})})
            try:response=urllib.request.urlopen(call,timeout=15)
            except urllib.error.HTTPError as error:response=error
            with response:status=response.status;raw=response.read(2_097_153);assert len(raw)<=2_097_152;reply=json.loads(raw)
            row={'sequence':len(rows),'service':kind,'method':method,'path':path,'route':route,'status':status,'started_utc':started,
                'elapsed_ms':1000*(time.perf_counter()-timer),'response_sha256':sha256(raw).hexdigest()}
            journal.write(json.dumps(row)+'\n');journal.flush();rows.append(row)
            assert status==wanted,(kind,path,status)
            expected[(kind,method,str(status),route)]+=1
            if kind=='feature' and method=='POST' and status==200:
                assert reply['prediction']==first['response']['prediction'] and all(reply['model'][k]==v for k,v in rollout['model_pin'].items())
                expected[('predictor','POST','200','/v1/models/{model_name}:predict')]+=1
            if kind=='predictor' and method=='POST' and status==200:
                assert reply['predictions']==[first['response']['prediction']] and all(reply['model'][k]==v for k,v in rollout['model_pin'].items())
            key=kind+'-'+method+'-'+str(status)
            if key not in examples:examples[key]=reply
            return reply
        request('feature','GET','/v1/machines/bad/features','/v1/machines/{machine_id}/features',422)
        request('feature','GET','/v1/machines/P-9999/features','/v1/machines/{machine_id}/features',404)
        request('feature','POST','/v1/machines/'+stopped+':predict','/v1/machines/{machine_id}:predict',409,{})
        request('predictor','POST','/v1/models/iot-pump-failure-v1:predict','/v1/models/{model_name}:predict',422,{'instances':[{}]})
        request('predictor','GET','/v1/models/unknown','/v1/models/{model_name}',404)
        request('drift','POST','/v1/drift:compare','/v1/drift:compare',422,{})
        request('feature','GET','/v1/machines/'+first['machine_id']+'/features?privacy_probe=CW_CANARY28B','/v1/machines/{machine_id}/features',200,
            headers={'X-Coursework-Canary':'CW_HEADER28B'})
        started=time.monotonic();cycles=0
        while time.monotonic()-started<duration:
            request('feature','GET','/v1/machines/'+first['machine_id']+'/features','/v1/machines/{machine_id}/features',200)
            request('feature','POST','/v1/machines/'+first['machine_id']+':predict','/v1/machines/{machine_id}:predict',200,{})
            request('predictor','POST','/v1/models/iot-pump-failure-v1:predict','/v1/models/{model_name}:predict',200,{'instances':[first['features']['features']]})
            request('drift','POST','/v1/drift:compare','/v1/drift:compare',200,window)
            cycles+=1;time.sleep(.3)
    after=scrape('after');actual=Counter({k:after[k]-before[k] for k in set(after)|set(before) if after[k]!=before[k]})
    save('counter-after.json',[{'key':k,'value':v} for k,v in sorted(after.items())]);save('expected-deltas.json',[{'key':k,'value':v} for k,v in sorted(expected.items())])
    save('actual-deltas.json',[{'key':k,'value':v} for k,v in sorted(actual.items())]);save('response-examples.json',examples)
    assert actual==expected,{'actual':dict(actual),'expected':dict(expected)}
    save('verification.json',{'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'duration_seconds':duration,'cycles':cycles,
        'client_requests':len(rows),'sdk_requests_including_predictor_calls':sum(expected.values()),'counter_groups':len(expected),
        'http_status_counts':dict(Counter(str(r['status']) for r in rows)),'scope':'Actual client ledger and per-Pod SDK counter deltas. Predictor oracle includes one downstream call per successful feature prediction. One32row drift window replayed; no ground truth or throughput-capacity claim.'})
    print((output/'verification.json').read_text())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['plan','rollout','output']:parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--duration',type=int,default=60);args=parser.parse_args()
    run(json.loads(args.plan.read_bytes()),json.loads(args.rollout.read_bytes()),args.output,args.duration)
