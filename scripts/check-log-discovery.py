"""Verify real Pod replacement and collector restart against exact CRI timestamps/messages without mutating application deployments."""
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
import argparse,json,os,subprocess,sys,time,urllib.parse,urllib.request
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.log_discovery import probe_deployment


def nanos(value):
    """Convert original RFC3339 nanoseconds without float rounding."""
    whole,fraction=value.removesuffix('Z').split('.');return str(int(datetime.fromisoformat(whole).replace(tzinfo=timezone.utc).timestamp())*10**9+int(fraction.ljust(9,'0')))


def main(args):
    """Run two known twenty-line batches from different Pod UIDs and demand exact Loki content before/after restart."""
    r=args.runtime_root.resolve();out=args.output;out.mkdir(parents=True,exist_ok=False);os.environ['KUBECONFIG']='/etc/rancher/k3s/k3s.yaml';k=['/usr/local/bin/k3s','kubectl']
    def save(n,v):(out/n).write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')
    def call(n,cmd):
        p=subprocess.run(cmd,capture_output=True,timeout=190);(out/(n+'.stdout')).write_bytes(p.stdout);(out/(n+'.stderr')).write_bytes(p.stderr)
        if p.returncode:raise RuntimeError('Native log-discovery step failed: '+n)
        return p.stdout
    session=json.loads((r/'data/runtime/ci/session.json').read_bytes());assert (datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc)).total_seconds()>1800
    assert json.loads((r/'outputs/log-discovery-28/start/verification.json').read_bytes())['passed']
    retain_first=False
    if args.resume_from:
        previous=args.resume_from.resolve();assert previous.is_relative_to(r/'outputs/log-discovery-28') and not (previous/'second.cri').exists()
        current_pods=json.loads(subprocess.check_output(k+['get','pods','-n','iot-features','-l','app=iot-log-discovery-probe','-o','json']))['items']
        retain_first=(previous/'first.cri').exists()
        if retain_first:
            previous_pods=json.loads((previous/'first-pods.json').read_bytes())['items'];assert {p['metadata']['uid'] for p in current_pods}=={p['metadata']['uid'] for p in previous_pods}
            assert json.loads((previous/'first-deployment.json').read_bytes())==probe_deployment('first')
            (out/'prior-first.cri').write_bytes((previous/'first.cri').read_bytes())
        else:assert not current_pods
        deployment=json.loads(subprocess.check_output(k+['get','deployment','iot-log-discovery-probe','-n','iot-features','-o','json']));save('checkpoint-deployment.json',deployment)
        assert deployment['spec']['selector']['matchLabels']=={'app':'iot-log-discovery-probe'} and deployment['spec']['template']['spec']['containers'][0]['image']=='iot-coursework-predictor:observe-jenkins-2'
        save('resume.json',{'prior_output':str(previous.relative_to(r)),'first_pod_and_cri_retained':retain_first,'no_pod_or_cri_created':not retain_first,'only_controlled_probe_will_be_repaired':True})
    else:assert subprocess.run(k+['get','deployment','iot-log-discovery-probe','-n','iot-features'],capture_output=True).returncode!=0
    begun=int(nanos((previous/'first.cri').read_text().split(' ',1)[0])) if retain_first else time.time_ns();expected=[];uids=[]
    def query(label):
        expression='{host="vm-linux",collection="native-28-auto",service="log-probe"} |= "iot-log-discovery-28"'
        url='http://127.0.0.1:13100/loki/api/v1/query_range?'+urllib.parse.urlencode({'query':expression,'start':str(begun-10**9),'end':str(time.time_ns()),'limit':1000,'direction':'forward'})
        with urllib.request.urlopen(url,timeout=30) as response:payload=json.load(response)
        save(label+'.json',payload);assert payload['status']=='success';return [(s['stream']['pod_uid'],ts,message) for s in payload['data']['result'] for ts,message in s['values']]
    def wait_for(count,label):
        deadline=time.monotonic()+100
        for attempt in range(25):
            rows=query(label+'-'+str(attempt))
            if len(rows)>=count:assert Counter(rows)==Counter(expected);return rows
            if time.monotonic()>deadline:break
            time.sleep(4)
        raise RuntimeError('Loki did not contain exact expected '+str(count)+' lines')
    original_loki=int(subprocess.check_output(['systemctl','show','iot-loki-28','-p','MainPID','--value']))
    for batch in ['first','second']:
        manifest=probe_deployment(batch);save(batch+'-deployment.json',manifest)
        if not (batch=='first' and retain_first):call(batch+'-apply',k+['apply','-f',str(out/(batch+'-deployment.json'))]);call(batch+'-ready',k+['rollout','status','deployment/iot-log-discovery-probe','-n','iot-features','--timeout=90s'])
        pods=json.loads(subprocess.check_output(k+['get','pods','-n','iot-features','-l','app=iot-log-discovery-probe','-o','json']));save(batch+'-pods.json',pods);running=[p for p in pods['items'] if p['status']['phase']=='Running' and not p['metadata'].get('deletionTimestamp')];assert len(running)==1;p=running[0];uid=p['metadata']['uid'];assert uid not in uids;uids.append(uid)
        path=Path('/var/log/pods')/('iot-features_'+p['metadata']['name']+'_'+uid)/'probe/0.log';deadline=time.monotonic()+15
        while not path.exists():assert time.monotonic()<deadline;time.sleep(.2)
        time.sleep(1);raw=path.read_bytes();(out/(batch+'.cri')).write_bytes(raw);lines=raw.decode().splitlines();assert len(lines)==20
        if batch=='first' and retain_first:assert raw==(previous/'first.cri').read_bytes()
        for index,line in enumerate(lines):
            ts,stream,flag,message=line.split(' ',3);assert stream=='stdout' and flag=='F';content=json.loads(message);assert content=={'experiment':'iot-log-discovery-28','batch':batch,'sequence':index,'pod_uid':uid};expected.append((uid,nanos(ts),message))
        wait_for(len(expected),batch+'-loki')
    save('expected.json',expected);before=call('collector-before',['systemctl','show','iot-alloy-discovery-28','-p','MainPID','--value']).decode().strip();call('collector-restart',['systemctl','restart','iot-alloy-discovery-28']);time.sleep(12);after=call('collector-after',['systemctl','show','iot-alloy-discovery-28','-p','MainPID','--value']).decode().strip();assert before!=after and after!='0';wait_for(40,'after-restart-loki')
    assert int(subprocess.check_output(['systemctl','show','iot-loki-28','-p','MainPID','--value']))==original_loki
    sources=[]
    for ns in ['iot-features','iot-serving']:
        for p in json.loads(subprocess.check_output(k+['get','pods','-n',ns,'-o','json']))['items']:
            labels=p['metadata']['labels'];service={'iot-feature-api':'feature-api','iot-drift-api':'drift-api','iot-ab-serve-jenkins-8':'ab-api'}.get(labels.get('app.kubernetes.io/instance'))
            if labels.get('serving.kserve.io/inferenceservice')=='iot-pump-serve-jenkins-8':service='predictor'
            if not service:continue
            uid=p['metadata']['uid'];expr='{collection="native-28-auto",service="'+service+'",pod_uid="'+uid+'"}';start=int(datetime.fromisoformat(p['metadata']['creationTimestamp']).timestamp()*10**9)-10**9
            url='http://127.0.0.1:13100/loki/api/v1/query_range?'+urllib.parse.urlencode({'query':expr,'start':str(start),'end':str(time.time_ns()),'limit':20,'direction':'forward'})
            with urllib.request.urlopen(url,timeout=30) as response:payload=json.load(response)
            save('application-'+service+'.json',payload);assert payload['status']=='success' and payload['data']['result'];sources.append({'service':service,'pod':p['metadata']['name'],'uid':uid,'streams':len(payload['data']['result'])})
    assert {s['service'] for s in sources}=={'feature-api','drift-api','ab-api','predictor'}
    save('application-sources.json',sources);call('collector-status',['systemctl','show','iot-alloy-discovery-28','-p','ActiveState','-p','MemoryMax','-p','MemorySwapMax','-p','CPUQuotaPerSecUSec','-p','NoNewPrivileges','-p','MainPID','-p','MemoryCurrent']);call('collector-journal',['journalctl','-u','iot-alloy-discovery-28','--no-pager','-n','100'])
    save('verification.json',{'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'probe_pod_uids':uids,'raw_cri_lines':40,'loki_lines':40,'exact_timestamp_message_identity_match':True,'collector_pid_changed':True,'loki_pid_unchanged':True,'duplicate_rows_after_restart':0,'current_application_services':sources,'scope':'Two controlled real Pod replacements, scoped discovery, exact known forty lines and one collector restart. No exactly-once guarantee for arbitrary crashes, rotation or multi-node claim.'});print((out/'verification.json').read_text())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--resume-from',type=Path);main(p.parse_args())
