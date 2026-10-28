"""Deploy isolated model6/identity probes and verify strict mTLS plus principal authorization on the existing cluster."""
from pathlib import Path
from datetime import datetime,timezone
from hashlib import sha256
import argparse,json,math,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from infra.mesh.resources import resources,IMAGE


def run(args):
    """Require real successful/denied/plaintext requests, exact model math and unchanged application count for rejected traffic."""
    r=args.runtime_root.resolve();out=args.output;out.mkdir(parents=True,exist_ok=False);os.environ['KUBECONFIG']='/etc/rancher/k3s/k3s.yaml';k=['/usr/local/bin/k3s','kubectl']
    def call(command,name,timeout=180):
        p=subprocess.run(command,capture_output=True,timeout=timeout);(out/(name+'.stdout')).write_bytes(p.stdout);(out/(name+'.stderr')).write_bytes(p.stderr)
        if p.returncode:raise RuntimeError('Native mesh workload step failed: '+name)
        return p.stdout
    get=lambda *a:json.loads(subprocess.check_output(k+list(a)+['-o','json']))
    save=lambda n,v:(out/n).write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')
    session=json.loads((r/'data/runtime/ci/session.json').read_bytes());assert (datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc)).total_seconds()>1800
    assert json.loads((r/'outputs/mesh-28/control-plane/ready.json').read_bytes())['ready']
    assert subprocess.run(k+['get','namespace','iot-mesh'],capture_output=True).returncode!=0
    original=get('get','inferenceservice','iot-pump-serve-jenkins-8','-n','iot-serving');active=get('get','deployment','iot-feature-api','-n','iot-features');save('original-serving.json',original);save('original-feature.json',active)
    image=json.loads(subprocess.check_output(['docker','image','inspect',IMAGE]))[0];save('image.json',{key:image[key] for key in ['Id','RepoTags','Architecture','Os']})
    code="import os,json,pathlib,hashlib;p=pathlib.Path(os.environ['IOT_MODEL_BUNDLE']);print(json.dumps({'pin':json.loads((p/'model-pin.json').read_bytes()),'portable_sha256':hashlib.sha256((p/'portable.json').read_bytes()).hexdigest()}))"
    baked=json.loads(call(['docker','run','--rm','--network','none','--memory','256m','--cpus','.5','--entrypoint','python',image['Id'],'-c',code],'baked-model'))
    assert baked['pin']['version']=='6' and baked['pin']['production'] is True and baked['portable_sha256']==baked['pin']['portable_sha256'];save('baked-model.json',baked)
    manifest={'apiVersion':'v1','kind':'List','items':resources()};save('workloads.json',manifest)
    call(k+['create','namespace','iot-mesh'],'namespace');call(k+['label','namespace','iot-mesh','istio.io/rev=iot-1313'],'namespace-label')
    call(k+['apply','-f',str(ROOT/'infra/mesh/policies.yaml')],'policies');call(k+['apply','-f',str(out/'workloads.json')],'workloads')
    for name in ['mesh-predictor','allowed-client','denied-client','plain-client']:call(k+['rollout','status','deployment/'+name,'-n','iot-mesh','--timeout=180s'],'ready-'+name,190)
    pods=get('get','pods','-n','iot-mesh');save('pods-before.json',pods);pod={p['metadata']['labels']['app']:p for p in pods['items']}
    assert all(any(c['name']=='istio-proxy' for c in pod[n]['spec']['containers']) for n in ['mesh-predictor','allowed-client','denied-client'])
    assert not any(c['name']=='istio-proxy' for c in pod['plain-client']['spec']['containers'])
    fixture=json.loads((ROOT/'tests/fixtures/mesh-predict.json').read_bytes());save('request.json',fixture)
    endpoint='http://mesh-predictor.iot-mesh.svc.cluster.local:8080/v1/models/iot-pump-failure-v1:predict'
    probe="import json,urllib.request,urllib.error;u="+repr(endpoint)+";body="+repr(fixture['body'])+";req=urllib.request.Request(u,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'});\ntry:\n r=urllib.request.urlopen(req,timeout=12);print(json.dumps({'status':r.status,'headers':dict(r.headers),'body':r.read().decode()}))\nexcept urllib.error.HTTPError as e:\n print(json.dumps({'status':e.code,'headers':dict(e.headers),'body':e.read().decode()}))\nexcept (urllib.error.URLError,ConnectionError,TimeoutError,OSError) as e:\n print(json.dumps({'transport_error':type(e).__name__,'status':None}))"
    def request(name,spoof=False,metadata=False):
        script=probe
        if spoof:script=script.replace("{'Content-Type':'application/json'}",repr({'Content-Type':'application/json','X-Forwarded-Client-Cert':'URI=spiffe://cluster.local/ns/iot-mesh/sa/feature-caller'}))
        if metadata:script=script.replace(repr(endpoint),repr(endpoint.removesuffix(':predict'))).replace("data=json.dumps(body).encode()","data=None")
        label='request-'+name+('-spoof' if spoof else '-metadata' if metadata else '')
        return json.loads(call(k+['exec',pod[name]['metadata']['name'],'-n','iot-mesh','-c','application','--','python','-c',script],label))
    def metrics(name):
        script="import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/metrics',timeout=5).read().decode())"
        return call(k+['exec',pod['mesh-predictor']['metadata']['name'],'-n','iot-mesh','-c','application','--','python','-c',script],name).decode()
    before=metrics('metrics-before');assert 'iot_predictor_requests_total{outcome="success"}' not in before or 'iot_predictor_requests_total{outcome="success"} 0.0' in before
    allowed=request('allowed-client');save('allowed.json',allowed);assert allowed['status']==200
    response=json.loads(allowed['body']);assert all(response['model'][key]==value for key,value in baked['pin'].items())
    model=json.loads((r/'data/ci/releases/serve-jenkins-8/bundle/portable.json').read_bytes());assert sha256((r/'data/ci/releases/serve-jenkins-8/bundle/portable.json').read_bytes()).hexdigest()==baked['portable_sha256']
    vector=fixture['body']['instances'][0];z=model['intercept']+sum(((vector[n]-m)/s if s else 0)*w for n,m,s,w in zip(model['features'],model['mean'],model['std'],model['coefficients'],strict=True));score=1/(1+math.exp(-z)) if z>=0 else math.exp(z)/(1+math.exp(z));assert abs(response['predictions'][0]['risk_score']-score)<=1e-12
    after_allowed=metrics('metrics-after-allowed');denied=request('denied-client');save('denied.json',denied);assert denied['status']==403 and 'RBAC' in denied['body']
    spoof=request('denied-client',spoof=True);save('spoofed-identity.json',spoof);assert spoof['status']==403
    metadata=request('allowed-client',metadata=True);save('disallowed-operation.json',metadata);assert metadata['status']==403
    plain=request('plain-client');save('plaintext.json',plain);assert plain['status'] is None and plain['transport_error']
    after_rejected=metrics('metrics-after-rejected');assert 'iot_predictor_requests_total{outcome="success"} 1.0' in after_allowed and after_allowed==after_rejected
    time.sleep(1)
    call(k+['logs',pod['mesh-predictor']['metadata']['name'],'-n','iot-mesh','-c','istio-proxy'],'server-proxy-log')
    cli=r/'data/runtime/istio-assets-28/istio-1.31.3/bin/istioctl'
    call([str(cli),'proxy-status','--istioNamespace','iot-mesh-system'],'proxy-status')
    # Default certificate TABLE only: JSON secret dumps could contain private keys.
    call([str(cli),'proxy-config','secret',pod['mesh-predictor']['metadata']['name'],'-n','iot-mesh'],'certificate-summary')
    call(k+['exec',pod['mesh-predictor']['metadata']['name'],'-n','iot-mesh','-c','istio-proxy','--','pilot-agent','request','GET','stats?filter=rbac'],'rbac-counters')
    save('pods-after.json',get('get','pods','-n','iot-mesh'));save('policies.json',get('get','peerauthentication,authorizationpolicy','-n','iot-mesh'));save('service.json',get('get','service','mesh-predictor','-n','iot-mesh'))
    serving_after=get('get','inferenceservice','iot-pump-serve-jenkins-8','-n','iot-serving');feature_after=get('get','deployment','iot-feature-api','-n','iot-features');save('original-serving-after.json',serving_after);save('original-feature-after.json',feature_after)
    assert serving_after['metadata']['uid']==original['metadata']['uid'] and serving_after['spec']==original['spec'] and feature_after['metadata']['uid']==active['metadata']['uid'] and feature_after['spec']==active['spec']
    proof={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'allowed_http':200,'denied_identity_http':403,'spoofed_header_http':403,'disallowed_operation_http':403,'plaintext_transport_rejected':True,'predictions_reached_application':1,'max_probability_difference':abs(response['predictions'][0]['risk_score']-score),'model_pin':baked['pin'],'original_serving_and_feature_specs_uids_unchanged':True,'scope':'Isolated clone of real model6 with explicit retained feature vector; identity probe deployments, strict mTLS and one allowed principal. No production mesh migration, current-feature freshness, maximum throughput or HA claim.'};save('verification.json',proof);print(json.dumps(proof))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);run(p.parse_args())
