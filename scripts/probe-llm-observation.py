"""Measure real GPU-stream output/usage and durable replay before deploying the LLM observation sidecar."""
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import argparse,base64,http.client,json,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from llm_observability.contracts import MODEL,PromptPolicy,StreamObservation
from llm_observability.ledger import TelemetryLedger

SOURCE_FILES={'llm_observability/__init__.py','llm_observability/contracts.py','llm_observability/ledger.py',
              'scripts/probe-llm-observation.py','tests/test_llm_observation_contracts.py','tests/test_llm_telemetry_ledger.py'}
QUESTIONS=[
    'Explain briefly why duplicate pump sensor events can distort a rolling vibration average.',
    'Explain briefly why high vibration alone does not prove a pump failure.',
    'Explain briefly why a prediction must identify its model version and feature timestamp.']


def run(args):
    """Use the actual retained router/GPU session and save original known synthetic requests, stream bytes and timing facts."""
    root=args.runtime_root.resolve();out=args.output.resolve()
    if not root.is_relative_to('/srv/iot-data') or not out.is_relative_to(root/'outputs'):raise ValueError('Use the retained lab output directory')
    seal=json.loads((ROOT.parent/'manifest.json').read_bytes());assert seal['format']=='iot-llm-observation-v1' and set(seal['files'])==SOURCE_FILES
    for name,digest in seal['files'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    gpu=json.loads((root/'data/runtime/ci/gpu-session.json').read_bytes())
    assert gpu['status']=='RUNNING' and gpu['termination_action']=='STOP' and gpu['max_duration_seconds']==28800
    assert datetime.fromisoformat(gpu['last_start_utc'])+timedelta(seconds=28800)>datetime.now(timezone.utc)+timedelta(minutes=10)
    out.mkdir(parents=True,exist_ok=False);k=['/usr/local/bin/k3s','kubectl']
    def save(name,value):
        """Write bounded original context and derived measurements without any authentication material."""
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    service=json.loads(subprocess.check_output(k+['get','service','iot-router-epp','-n','iot-llm','-o','json'],stderr=subprocess.PIPE))
    pods=json.loads(subprocess.check_output(k+['get','pods','-n','iot-llm','-l','app.kubernetes.io/instance=iot-model-gpu','-o','json'],stderr=subprocess.PIPE))
    live=[p for p in pods['items'] if p['status'].get('phase')=='Running' and not p['metadata'].get('deletionTimestamp')]
    assert len(live)==1 and all(c['ready'] and c.get('state',{}).get('running') for c in live[0]['status']['containerStatuses'])
    save('router-service.json',service);save('gpu-pods-before.json',pods);save('gpu-session.json',gpu);save('source.json',seal)
    secret=json.loads(subprocess.check_output(k+['get','secret','iot-local-model-client','-n','iot-agents','-o','json'],stderr=subprocess.PIPE))
    token=base64.b64decode(secret['data']['API_KEY']).decode();assert len(token)>=32
    authorization='Bearer '+token;store=TelemetryLedger(out/'telemetry.sqlite');measurements=[]
    for index,question in enumerate(QUESTIONS):
        body={'model':MODEL,'stream':True,'stream_options':{'include_usage':True},'max_tokens':128,'temperature':0,'seed':7,
              'messages':[{'role':'system','content':'Answer in at most three short sentences about the simulated IoT coursework. Do not invent measurements.'},
                          {'role':'user','content':question}]}
        assert PromptPolicy().inspect(body)==[];save(str(index)+'-request.json',body);request_id=uuid4().hex
        connection=http.client.HTTPConnection(service['spec']['clusterIP'],8081,timeout=150);started=time.perf_counter()
        connection.request('POST','/v1/chat/completions',body=json.dumps(body).encode(),headers={'Content-Type':'application/json','Accept':'text/event-stream','Authorization':authorization})
        response=connection.getresponse();header_elapsed=time.perf_counter()-started
        assert response.status==200 and response.getheader('Content-Type','').startswith('text/event-stream')
        observer=StreamObservation();chunks=[];total=0
        while True:
            chunk=response.read1(16384)
            if not chunk:break
            total+=len(chunk);assert total<=1048576;chunks.append(chunk);observer.feed(chunk,time.perf_counter()-started)
        measured=observer.finish(time.perf_counter()-started);connection.close();assert measured['complete'] and measured['ttft_seconds']>=header_elapsed
        (out/(str(index)+'-response.sse')).write_bytes(b''.join(chunks))
        fact={'agent':'direct','model':MODEL,'duration_seconds':measured['duration_seconds'],'ttft_seconds':measured['ttft_seconds'],
              'usage':measured['usage'],'status':200,'result':'ok','streaming':True,'finish_reason':measured['finish_reason'],'policy_categories':[]}
        assert store.put('request',request_id,fact) and store.put('request',request_id,fact) is False
        measurement={'request_id':request_id,'question_index':index,'http_status':response.status,'headers_seconds':header_elapsed,
            'response_bytes':total,'response_sha256':sha256(b''.join(chunks)).hexdigest(),'observation':measured,'fact':fact}
        measurements.append(measurement);save('measurements.json',measurements)
    before=store.rows();store.close();store=TelemetryLedger(out/'telemetry.sqlite')
    for measurement in measurements:assert store.put('request',measurement['request_id'],measurement['fact']) is False
    assert store.rows()==before and len(before)==3;save('ledger-readback.json',before);store.close()
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':seal['commit'],'source_files':6,
            'native_requests':3,'actual_reported_usage':[m['fact']['usage'] for m in measurements],
            'observed_ttft_seconds':[m['fact']['ttft_seconds'] for m in measurements],
            'full_response_seconds':[m['fact']['duration_seconds'] for m in measurements],'ledger_rows_after_replay_and_reopen':3,
            'upstream_bytes_edited':False,'scope':'Three actual private-router GPU streaming requests and durable measurement/replay proof. No new sidecar deployment, dashboard, safety enforcement, agent tracing or full LLM observability claim.'}
    save('verification.json',report)
    variants=[token.encode(),authorization.encode(),base64.b64encode(token.encode())]
    for path in out.iterdir():
        if path.is_file():assert not any(v in path.read_bytes() for v in variants),'Credential-bearing measurement refused'
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runtime-root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);run(parser.parse_args())
