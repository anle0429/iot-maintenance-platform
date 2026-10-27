"""Observe real private API rolling updates and Helm3 atomic fallback while continuously checking functional HTTP."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, urlopen
ROOT=Path(__file__).resolve().parents[1]


def run(args):
    """Require healthy responses, old/new pod overlap, an actual bad-image state and a deployed rollback revision."""
    if args.output.exists():raise ValueError('Preserve existing rollout evidence')
    args.output.mkdir(parents=True)
    kubectl=[args.k3s,'kubectl'];release='iot-'+args.api+'-api';namespace='iot-features'
    def get(resource, *extra):
        """Read current Kubernetes objects without exporting kubeconfig or cluster credentials."""
        return json.loads(subprocess.check_output(kubectl+['get',resource,*extra,'-n',namespace,'-o','json'],text=True))
    def history():
        """Read native Helm revision status after each action, rather than inferring rollback from exit code."""
        return json.loads(subprocess.check_output([args.helm,'history',release,'-n',namespace,'-o','json'],text=True))
    service=get('service',release);base='http://'+service['spec']['clusterIP']
    body=None
    if args.api=='drift':
        bundle=json.loads(args.reference.read_text());reference=sha256(json.dumps(bundle,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        body=json.dumps({'reference_id':reference,'window_id':args.run,'start':'2026-10-01T00:00:00Z','end':'2026-10-01T01:00:00Z',
             'observations':[{'id':f'rollout-{i}','event_timestamp':'2026-10-01T00:30:00Z','available_at':'2026-10-01T00:31:00Z','features':row} for i,row in enumerate(bundle['samples'][:32])]}).encode()
        path='/v1/drift:compare'
    else:path='/v1/machines/'+args.machine+':predict'
    def probe():
        """Check a real cached drift comparison or native Feast-to-KServe prediction on every sample."""
        request=Request(base+path,data=body,method='POST',headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=5) as response:
            value=json.load(response);assert response.status==200
        with urlopen(base+'/health/ready',timeout=5) as response:assert response.status==200
        return sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    expected=probe();before=get('deployment',release);assert before['status']['availableReplicas']>=1
    strategy=before['spec']['strategy'];assert strategy['type']=='RollingUpdate' and strategy['rollingUpdate']=={'maxSurge':1,'maxUnavailable':0}
    (args.output/'before-deployment.json').write_text(json.dumps(before,indent=2)+'\n')
    (args.output/'before-history.json').write_text(json.dumps(history(),indent=2)+'\n')
    phases={}
    for phase,tag in [('rolling','v1'),('bad-image','intentionally-absent-atomic-probe')]:
        folder=args.output/phase;command=[sys.executable,str(ROOT/'scripts/deploy-api.py'),'--api',args.api,'--helm',args.helm,
             '--revision',args.run+'-'+phase,'--image-tag',tag,'--timeout-seconds','60','--output',str(folder)]
        observations=[]
        with (args.output/(phase+'-controller.txt')).open('w') as log:
            process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+240
            try:
                while process.poll() is None:
                    if time.monotonic()>deadline:raise TimeoutError('Bounded rollback observer expired')
                    error=None
                    try:signature=probe();assert signature==expected
                    except Exception as failure:error=type(failure).__name__+': '+str(failure)
                    pods=get('pods','-l','app.kubernetes.io/instance='+release)['items']
                    summaries=[]
                    for pod in pods:
                        summaries.append({'uid':pod['metadata']['uid'],'name':pod['metadata']['name'],
                             'revision':pod['metadata']['annotations']['iot.coursework/revision-marker'],
                             'phase':pod['status']['phase'],'deleting':bool(pod['metadata'].get('deletionTimestamp')),
                             'containers':[{'image':state['image'],'ready':state['ready'],'state':state['state']} for state in pod['status'].get('containerStatuses',[])]})
                    observations.append({'utc':datetime.now(timezone.utc).isoformat(),'functional_http_passed':error is None,'error':error,'pods':summaries})
                    time.sleep(.5)
                code=process.wait(timeout=5)
            except BaseException:
                # This stops only our observer child if it hangs; native Helm output is retained for diagnosis.
                process.terminate();process.wait(timeout=10);raise
        records=history();after=get('deployment',release)
        (args.output/(phase+'-observations.json')).write_text(json.dumps(observations,indent=2)+'\n')
        (args.output/(phase+'-history.json')).write_text(json.dumps(records,indent=2)+'\n')
        (args.output/(phase+'-deployment.json')).write_text(json.dumps(after,indent=2)+'\n')
        assert observations and all(row['functional_http_passed'] for row in observations)
        assert probe()==expected and after['status']['availableReplicas']>=1
        if phase=='rolling':
            assert code==0 and records[-1]['status']=='deployed'
            assert after['spec']['template']['metadata']['annotations']['iot.coursework/revision-marker']==args.run+'-rolling'
            assert any(len([p for p in row['pods'] if not p['deleting']])>=2 for row in observations)
        else:
            assert code!=0 and records[-1]['status']=='deployed' and 'Rollback to' in records[-1]['description']
            assert any(row['status']=='failed' for row in records)
            assert after['spec']['template']['metadata']['annotations']['iot.coursework/revision-marker']==args.run+'-rolling'
            assert all(container['image'].endswith(':v1') for container in after['spec']['template']['spec']['containers'])
            assert any(state.get('state',{}).get('waiting',{}).get('reason')=='ErrImageNeverPull' for row in observations for pod in row['pods'] for state in pod['containers'])
        phases[phase]={'exit_code':code,'http_samples':len(observations),'failed_http_samples':sum(not row['functional_http_passed'] for row in observations),
                       'final_revision':records[-1]['revision'],'final_status':records[-1]['status'],'final_description':records[-1]['description']}
    report={'passed':True,'api':args.api,'release':release,'phases':phases,'functional_response_sha256':expected,
            'strategy':strategy,'completed_utc':datetime.now(timezone.utc).isoformat(),
            'source_sha256':{name:sha256((ROOT/name).read_bytes()).hexdigest() for name in ['scripts/verify-api-rollout.py','scripts/deploy-api.py','infra/charts/iot-api/templates/deployment.yaml']},
            'scope':'Actual configuration-marker rolling update followed by intentionally absent image and native Helm atomic rollback. Same application/model bytes for good revision; functional HTTP sampled throughout, not a zero-downtime SLA or code-version benchmark.'}
    (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--api',choices=['feature','drift'],required=True)
    parser.add_argument('--helm',default='helm');parser.add_argument('--k3s',default='/usr/local/bin/k3s')
    parser.add_argument('--reference',type=Path);parser.add_argument('--machine',default='P-001');parser.add_argument('--run',required=True)
    parser.add_argument('--output',type=Path,required=True);run(parser.parse_args())
