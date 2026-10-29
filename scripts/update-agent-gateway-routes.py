"""Correct only the two owned agent ingress header annotations, retaining the rejected native predecessor."""
from hashlib import sha256
from pathlib import Path
import argparse,json,socket,ssl,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from infra.gateway.agents import ingresses,ROUTES


def run(args):
    """Guard each original ingress UID/spec and require its actual CA/SNI-verified leaf before accepting the correction."""
    out=args.output;out.mkdir(parents=True,exist_ok=False);k=['/usr/local/bin/k3s','kubectl'];results={}
    def get(*values):
        """Read one original Kubernetes object without reading its credential Secrets."""
        return json.loads(subprocess.check_output(k+list(values)+['-o','json'],stderr=subprocess.PIPE))
    for expected in ingresses():
        name=expected['metadata']['name'];before=get('get','ingress',name,'-n','iot-agents')
        assert before['spec']==expected['spec']
        previous=before['metadata']['annotations']['nginx.org/proxy-set-headers']
        assert previous in ['Authorization:,X-User-Id: $remote_user,X-Forwarded-User: $remote_user',
                            'Authorization:,X-User-Id: coursework,X-Forwarded-User: coursework']
        for key,value in expected['metadata']['annotations'].items():
            if key!='nginx.org/proxy-set-headers':assert before['metadata']['annotations'][key]==value
        (out/(name+'-before.json')).write_text(json.dumps(before,indent=2)+'\n')
        patch=[{'op':'test','path':'/metadata/uid','value':before['metadata']['uid']},
               {'op':'test','path':'/metadata/annotations/nginx.org~1proxy-set-headers','value':previous},
               {'op':'replace','path':'/metadata/annotations/nginx.org~1proxy-set-headers','value':expected['metadata']['annotations']['nginx.org/proxy-set-headers']}]
        (out/(name+'-patch.json')).write_text(json.dumps(patch,indent=2)+'\n')
        native=subprocess.run(k+['patch','ingress',name,'-n','iot-agents','--type=json','--patch-file',str(out/(name+'-patch.json'))],capture_output=True,timeout=30)
        (out/(name+'-patch.txt')).write_bytes(native.stdout+native.stderr);assert native.returncode==0
        after=get('get','ingress',name,'-n','iot-agents');assert after['metadata']['uid']==before['metadata']['uid'] and after['spec']==before['spec']
        (out/(name+'-after.json')).write_text(json.dumps(after,indent=2)+'\n');results[name]={'uid':after['metadata']['uid'],'spec_preserved':True}
    gateway=get('get','service','iot-gateway','-n','iot-gateway');context=ssl.create_default_context(cafile=str(args.deployment/'ca.crt'))
    expected_leaf=sha256(ssl.PEM_cert_to_DER_cert((args.deployment/'server.crt').read_text())).hexdigest();tls={}
    for host,_,_ in ROUTES.values():
        deadline=time.monotonic()+60
        while True:
            try:
                with context.wrap_socket(socket.create_connection((gateway['spec']['clusterIP'],443),timeout=5),server_hostname=host) as peer:
                    actual=sha256(peer.getpeercert(binary_form=True)).hexdigest();assert actual==expected_leaf
                    tls[host]={'ca_verified':True,'hostname_verified':True,'leaf_sha256':actual};break
            except OSError:
                if time.monotonic()>=deadline:raise RuntimeError('The corrected ingress still does not serve its expected TLS identity') from None
                time.sleep(.5)
    (out/'tls-ready.json').write_text(json.dumps(tls,indent=2)+'\n')
    report={'passed':True,'source_sha256':{n:sha256((ROOT/n).read_bytes()).hexdigest() for n in ['scripts/update-agent-gateway-routes.py','infra/gateway/agents.py']},
            'resources':results,'tls':tls,'scope':'Two owned header corrections; same ingress UIDs, backends, certificates and controller. Full authentication/rate/UI proof follows separately.'}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['output','deployment']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
