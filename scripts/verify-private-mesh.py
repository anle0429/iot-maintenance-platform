"""Verify original native mTLS identity/authorization receipts, math and isolation with stdlib and Git only."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime
import json, math, subprocess
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'docs/evidence/private-mesh-linux'


def read(name):
    """Decode original bytes after manifest validation."""
    return json.loads((BASE/name).read_bytes())


def main():
    """Recompute results from responses, original Envoy logs/counters/certs and Pod identities."""
    manifest=read('manifest.json');files={p.relative_to(BASE).as_posix() for p in BASE.rglob('*') if p.is_file() and p.name!='manifest.json'}
    assert files==set(manifest['files']) and len(files)==106
    for n,v in manifest['files'].items():
        assert not Path(n).is_absolute() and '..' not in Path(n).parts
        raw=(BASE/n).read_bytes();assert len(raw)==v['bytes'] and sha256(raw).hexdigest()==v['sha256'] and b'PRIVATE KEY-----' not in raw,n
    native=read('native-manifest.json');assert len(native['files'])==103 and all(manifest['files'][n]==v for n,v in native['files'].items())
    total=0
    for label,commit,count in [('control','818271ee2a0c11800b45bfd8cce2f46e5fe48ac8',7),('first-workload','17e08d7c5fd7f65535a6b896c67de659448235c1',8),('second-workload','bcd757c08a99b0a5ef164c7236baf779c474f946',8),('resume','56dbf0faa7ca34b2d8f2eea183d8888b42d4069a',8)]:
        seal=read('sources/'+label+'.json');assert seal['commit']==commit and len(seal['files'])==count
        for n,h in seal['files'].items():assert sha256(subprocess.check_output(['git','show',commit+':'+n],cwd=ROOT)).hexdigest()==h;total+=1
    assert total==31
    for n,h in read('sources/resume.json')['files'].items():assert sha256((ROOT/n).read_bytes()).hexdigest()==h,n
    pins=json.loads((ROOT/'infra/mesh/assets.json').read_bytes());asset=read('preflight/download.json');assert asset['archive_sha256']==pins['archive_sha256'] and asset['archive_bytes']==30393605 and asset['version']['version']=='1.31.3'
    control=read('control-plane/ready.json');assert control['ready'] and control['rendered_resources']==32 and control['private_only'] and control['existing_namespace_labels_unchanged']
    old=read('control-plane/namespaces-before.json');new=read('control-plane/namespaces-after.json')
    labels=lambda v:{p['metadata']['name']:p['metadata'].get('labels',{}) for p in v['items']}
    assert all(labels(new)[n]==v for n,v in labels(old).items())
    for service in read('control-plane/services.json')['items']:assert service['spec']['type']=='ClusterIP' and not service['spec'].get('externalIPs')
    prefix='workloads-readback/';proof=read(prefix+'verification.json');assert proof['passed'] and proof['max_probability_difference']==0 and proof['predictions_reached_application']==1
    allowed=read(prefix+'allowed.json');assert allowed['status']==200;response=json.loads(allowed['body']);pin=read(prefix+'baked-model.json')['pin'];assert all(response['model'][k]==v for k,v in pin.items()) and pin['version']=='6' and pin['production']
    modelpath=ROOT/'docs/evidence/ci-api-observability/bundle/portable.json';assert sha256(modelpath.read_bytes()).hexdigest()==pin['portable_sha256'];model=json.loads(modelpath.read_bytes());request=read(prefix+'request.json');vector=request['body']['instances'][0]
    assert request==json.loads((ROOT/'tests/fixtures/mesh-predict.json').read_bytes()) and sha256((ROOT/request['parent']).read_bytes()).hexdigest()==request['parent_sha256']
    z=model['intercept']+sum(((vector[n]-m)/s if s else 0)*w for n,m,s,w in zip(model['features'],model['mean'],model['std'],model['coefficients'],strict=True));score=1/(1+math.exp(-z)) if z>=0 else math.exp(z)/(1+math.exp(z))
    assert abs(score-response['predictions'][0]['risk_score'])<=1e-12 and response['predictions'][0]['predicted_failure']==int(score>=pin['threshold'])
    for n in ['denied.json','spoofed-identity.json','disallowed-operation.json']:assert read(prefix+n)['status']==403 and 'RBAC' in read(prefix+n)['body']
    plain=read(prefix+'plaintext.json');assert plain['status'] is None and plain['reason_type'] in ['ConnectionResetError','RemoteDisconnected'] and plain['resolved_ip']==read(prefix+'service.json')['spec']['clusterIP']
    after=(BASE/prefix/'metrics-after-allowed.stdout').read_bytes();assert after==(BASE/prefix/'metrics-after-rejected.stdout').read_bytes() and b'iot_predictor_requests_total{outcome="success"} 1.0' in after
    records=[json.loads(line) for line in (BASE/prefix/'server-proxy-log.stdout').read_text().splitlines() if line.startswith('{')];assert len(records)==5
    principal='spiffe://cluster.local/ns/iot-mesh/sa/'
    assert [(v['source_principal'],v['status'],v['method'],v['details']) for v in records]==[(principal+'feature-caller',200,'POST','via_upstream'),(principal+'untrusted-caller',403,'POST','rbac_access_denied_matched_policy[none]'),(principal+'untrusted-caller',403,'POST','rbac_access_denied_matched_policy[none]'),(principal+'feature-caller',403,'GET','rbac_access_denied_matched_policy[none]'),(None,0,None,'filter_chain_not_found')]
    for n in ['rbac-counters.stdout','../review/stats.txt']:
        counters=(BASE/prefix/n).read_text();assert 'http.inbound_0.0.0.0_8080;.rbac.allowed: 1\n' in counters and 'http.inbound_0.0.0.0_8080;.rbac.denied: 3\n' in counters
    certs=read('review/certs.json')['certificates'];assert certs
    at=datetime.fromisoformat(proof['checked_utc'])
    for c in certs:
        chain=c['cert_chain'][0];assert chain['subject_alt_names']==[{'uri':principal+'model-server'}] and datetime.fromisoformat(chain['valid_from'])<at<datetime.fromisoformat(chain['expiration_time'])
        assert chain['serial_number']=='6f21c8ef752a1720657e39c6f0d5c240'
    table=(BASE/prefix/'certificate-summary.stdout').read_text();assert 'ACTIVE' in table and '6f21c8ef752a1720657e39c6f0d5c240' in table
    podsets=[read(prefix+n)['items'] for n in ['pods-before.json','pods-after.json']]+[read('workloads-v2/pods-before.json')['items'],read('final/mesh-pods.json')['items']]
    assert all({p['metadata']['uid'] for p in group}=={p['metadata']['uid'] for p in podsets[0]} for group in podsets)
    for p in podsets[0]:
        proxies=[c for c in p['spec'].get('initContainers',[]) if c['name']=='istio-proxy']
        if p['metadata']['labels']['app']=='plain-client':assert not proxies;continue
        assert len(proxies)==1 and proxies[0]['restartPolicy']=='Always'
        status=next(c for c in p['status']['initContainerStatuses'] if c['name']=='istio-proxy');assert status['ready'] and status['started'] and 'running' in status['state'] and status['restartCount']==0
        assert proxies[0]['image']=='docker.io/istio/proxyv2:1.31.3' and status['imageID'].endswith('@sha256:eb74eacea98d3fb1e239ea84016085155478586a554158676b9b67d960151084')
    resume=read(prefix+'resume.json');assert resume['read_only_resume'] and resume['pod_uids_unchanged'] and resume['no_prior_request_receipt']
    for n,h in resume['checkpoint_sha256'].items():assert sha256((BASE/'workloads-v2'/n).read_bytes()).hexdigest()==h
    for name in ['serving','feature']:
        a=read(prefix+'original-'+name+'.json');b=read(prefix+'original-'+name+'-after.json');assert a['metadata']['uid']==b['metadata']['uid'] and a['spec']==b['spec']
    assert 'model-pin.json' in (BASE/'workloads/baked-model.stderr').read_text() and not (BASE/'workloads/allowed.json').exists() and not (BASE/'workloads-v2/allowed.json').exists()
    for n in ['envoy-rbac.jpg','envoy-certificate.jpg']:assert (BASE/'screenshots'/n).read_bytes().startswith(b'\xff\xd8\xff')
    print('PASS106payload/2originalJPEG;31Gitbindings;Istio1.31.3/private32resources;native sidecars/strict mTLS;allowed200+exactmodel6math;identity/spoof/operation403;plaintextTLSreset;Envoy1allowed3denied/actualSPIFFE;appcount1;stablePodUIDs/originalservingspecs;failedpre-requestattemptsretained. Isolated lab, no production mesh migration/HA/certificate rotation claim.')


if __name__=='__main__':main()
