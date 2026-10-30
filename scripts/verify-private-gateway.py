"""Independently audit genuine private gateway routing/auth/TLS/rate evidence and unchanged model6 math."""
from git_sources import historical_blob
from pathlib import Path
from evidence_integrity import current_source_bindings
from hashlib import sha256
from urllib.parse import urlsplit
import json,math,ssl,subprocess,sys
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'docs/evidence/private-gateway-linux';sys.path.insert(0,str(ROOT))
from infra.gateway.resources import resources,HOSTS


def read(name):
    """Read native JSON only after the capsule bytes have been checked."""
    return json.loads((BASE/name).read_bytes())


def main():
    """Recompute counts, identity bindings and predictions without a VM, network, credentials or third-party packages."""
    manifest=read('manifest.json');files={p.relative_to(BASE).as_posix() for p in BASE.rglob('*') if p.is_file() and p!=BASE/'manifest.json'}
    assert files==set(manifest['files']) and len(files)==86
    for n,v in manifest['files'].items():
        assert not Path(n).is_absolute() and '..' not in Path(n).parts
        raw=(BASE/n).read_bytes();assert len(raw)==v['bytes'] and sha256(raw).hexdigest()==v['sha256'],n
        assert b'PRIVATE KEY-----' not in raw,n
    native=read('native-manifest.json');assert len(native['files'])==77 and all(manifest['files'][n]==v for n,v in native['files'].items())
    for source in (BASE/'sources').glob('*.json'):
        seal=json.loads(source.read_bytes())
        for n,h in seal['files'].items():assert sha256(historical_blob(ROOT, seal['commit']+':'+n)).hexdigest()==h
    final_source=read('sources/gateway-source-v4.json');assert len(final_source['files'])==5
    current_source_bindings(final_source)
    assets=json.loads((ROOT/'infra/gateway/assets.json').read_bytes());before=read('deploy/before.json');rendered=read('header-guard-fixed/manifests.json')['items']
    assert rendered==resources(assets['image'],before['bridge_ip'])
    service=read('final/service.json');assert service['spec']['type']=='ClusterIP' and not service['spec'].get('externalIPs')
    deployment=read('final/deployment.json');pod=read('final/pods.json')['items'];assert len(pod)==1 and deployment['status']['availableReplicas']==1
    assert pod[0]['spec']['containers'][0]['image']==assets['image'] and all(c['ready'] and c['restartCount']==0 for c in pod[0]['status']['containerStatuses'])
    assert not pod[0]['spec'].get('hostNetwork',False) and pod[0]['spec']['containers'][0]['resources']['limits']=={'cpu':'1','memory':'512Mi'}
    rows=read('verification-final/routing-auth.json');assert len(rows)==16
    for name,host in HOSTS.items():
        values=[v for v in rows if v['host']==host];assert len(values)==4
        assert sorted(v['status'] for v in values)==[200,308,401,401]
        for v in values:
            if v['status']==308:
                target=urlsplit(v['headers']['Location']);assert target.scheme=='https' and target.hostname==host and (target.port or 443)==443 and not target.query
            else:assert v['tls']=={'version':'TLSv1.3','ca_verified':True,'hostname_verified':True}
        reply=json.loads(next(v['body'] for v in values if v['status']==200))
        if name in ['metrics','logs']:assert reply['dashboard']['uid']==('iot-api-observability' if name=='metrics' else 'iot-linux-logs')
        if name=='features':assert reply['ready'] and reply['clock_mode']=='simulation'
        if name=='traces':assert set(reply['services'])>={'iot-feature-api','iot-predictor-api','iot-drift-api'}
    labels=read('verification-final/logs-native-query.json');assert labels['status']==200 and 'service' in json.loads(labels['body'])['data']
    negative=read('verification-final/tls-negative.json');assert len(negative)==2 and all(n['rejected'] for n in negative)
    assert negative[0]['type']=='SSLCertVerificationError' and negative[0]['reason']=='CERTIFICATE_VERIFY_FAILED' and negative[1]['reason']=='TLSV1_UNRECOGNIZED_NAME'
    cert=ssl._ssl._test_decode_cert(str(BASE/'deploy/server.crt'));assert {v for k,v in cert['subjectAltName'] if k=='DNS'}==set(HOSTS.values())
    assert ssl.PEM_cert_to_DER_cert((BASE/'deploy/ca.crt').read_text())
    burst=read('verification-final/burst.json');assert len(burst['responses'])==32 and burst['duration_seconds']>0
    statuses={str(c):sum(r['status']==c for r in burst['responses']) for c in set(r['status'] for r in burst['responses'])};assert statuses=={'200':6,'429':26}
    proof=read('verification-final/verification.json');assert proof['passed'] and proof['burst_statuses']==statuses and proof['burst_requests']==32
    assert read('verification-final/recovered.json')['status']==200
    gateway=read('verification-final/feature-http.json');direct=read('verification-final/feature-direct.json');assert gateway['status']==direct['status']==200 and json.loads(gateway['body'])==json.loads(direct['body'])
    config=(BASE/'verification-final/nginx-config.txt').read_text();assert config.count('proxy_set_header Authorization "";')==4 and 'rate=5r/s' in config and 'limit_req_status 429;' in config and 'burst=5 nodelay;' in config and 'ssl_protocols TLSv1.2 TLSv1.3;' in config
    assert len(read('final/rbac-checks.json'))==6 and sum(v['allowed'] for v in read('final/rbac-checks.json'))==3
    refresh=read('feature-refresh/verification.json');assert refresh['passed'] and refresh['model_version']=='6' and refresh['operating_predictions']==195 and refresh['stopped_conflicts']==5
    model=read('ui-supplement/portable.json');pin=read('ui-supplement/model-pin.json');assert sha256((BASE/'ui-supplement/portable.json').read_bytes()).hexdigest()==pin['portable_sha256']==refresh['portable_sha256'] and pin['production'] is True
    total=stopped=0
    for row in read('feature-refresh/http-all-machines.json'):
        if row['prediction_status']==409:stopped+=1;continue
        assert row['prediction_status']==200 and all(row['response']['model'][k]==v for k,v in pin.items())
        vector=row['features']['features'];z=model['intercept']+sum(((vector[n]-m)/s if s else 0)*w for n,m,s,w in zip(model['features'],model['mean'],model['std'],model['coefficients'],strict=True));score=1/(1+math.exp(-z)) if z>=0 else math.exp(z)/(1+math.exp(z))
        assert abs(score-row['response']['prediction']['risk_score'])<=1e-12 and row['response']['prediction']['predicted_failure']==int(score>=pin['threshold']);total+=1
    assert (total,stopped)==(195,5)
    trace=read('ui-supplement/trace-713708c.json')['result']['resourceSpans'];spans=[s for r in trace for scope in r['scopeSpans'] for s in scope['spans']];assert len(spans)==1 and spans[0]['traceId']=='713708c588a6f00dad1dbfdf8f76cb9b' and spans[0]['name']=='GET /v1/machines/{machine_id}/features'
    assert next(a['value']['intValue'] for a in spans[0]['attributes'] if a['key']=='http.status_code')=='200'
    assert 'Rejected' in (BASE/'header-diagnosis/controller.log').read_text() and 'Authorization' in (BASE/'header-diagnosis/controller.log').read_text()
    old=read('verification-fixed/routing-auth.json');assert old[-1]['status']==308 and ':443/' in old[-1]['headers']['Location']
    assert next(r['status'] for r in read('probe/responses.json') if r['host']==HOSTS['features'] and r['auth']=='correct')==502
    review=read('review-encoding/receipt.json');assert review['upstream_tls_ca_and_hostname_verified'] and review['listen']=='127.0.0.1 only'
    for n in ['grafana-gateway.jpg','logs-gateway.jpg','jaeger-gateway.jpg','features-gateway.jpg']:assert (BASE/'screenshots'/n).read_bytes().startswith(b'\xff\xd8\xff')
    print('PASS86payload/4originalJPEG;5Gitinputs/current manifests;4private routes/8auth401/4HTTPS308/TLS1.3+CA+SNI/2negativecases;native32burst6accepted26rejected429/recovered200;exactbackendfeature;195model6math+5stopped;actualtrace200;RBAC+private listener/source/failed attempts retained. Review bridge is loopback HTTP to authenticated verified HTTPS; no public DNS/Internet or browser-trusted TLS claim.')


if __name__=='__main__':main()
