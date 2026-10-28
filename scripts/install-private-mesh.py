"""Install only a revision-scoped minimal Istio control plane on the approved existing VM."""
from pathlib import Path
from datetime import datetime,timezone
from hashlib import sha256
import argparse,json,os,subprocess
ROOT=Path(__file__).resolve().parents[1]


def json_objects(raw):
    """Decode kubectl's client-only multi-object JSON output without a YAML runtime dependency."""
    decoder=json.JSONDecoder();values=[];text=raw.decode();offset=0
    while offset<len(text):
        while offset<len(text) and text[offset].isspace():offset+=1
        if offset==len(text):break
        value,offset=decoder.raw_decode(text,offset);values.extend(value['items'] if value.get('kind')=='List' else [value])
    return values


def run(args):
    """Validate generated namespaces/private services before installation; never label existing workloads for injection."""
    r=args.runtime_root.resolve();out=args.output;out.mkdir(parents=True,exist_ok=False);os.environ['KUBECONFIG']='/etc/rancher/k3s/k3s.yaml'
    pins=json.loads((ROOT/'infra/mesh/assets.json').read_bytes());session=json.loads((r/'data/runtime/ci/session.json').read_bytes())
    if not r.is_relative_to('/srv/iot-data') or (datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc)).total_seconds()<1800:raise ValueError('Use the current approved VM session with30minutes left')
    bundle=r/'data/runtime/istio-assets-28/istio-1.31.3';cli=bundle/'bin/istioctl';archive=bundle.parent/'istio.tar.gz'
    if sha256(archive.read_bytes()).hexdigest()!=pins['archive_sha256']:raise ValueError('Official Istio archive differs')
    def call(command,name,timeout=300):
        p=subprocess.run(command,capture_output=True,timeout=timeout);(out/(name+'.stdout')).write_bytes(p.stdout);(out/(name+'.stderr')).write_bytes(p.stderr)
        if p.returncode:raise RuntimeError('Native mesh step failed: '+name)
        return p.stdout
    k=['/usr/local/bin/k3s','kubectl'];get=lambda *a:json.loads(subprocess.check_output(k+list(a)+['-o','json']))
    before=get('get','namespaces');names={n['metadata']['name'] for n in before['items']};assert not names&{'iot-mesh-system','iot-mesh'}
    (out/'namespaces-before.json').write_text(json.dumps(before,indent=2)+'\n')
    version=json.loads(call([str(cli),'version','--remote=false','-o','json'],'client-version'));assert version['clientVersion']['version']==pins['version']
    generated=call([str(cli),'manifest','generate','-f',str(ROOT/'infra/mesh/istio-operator.yaml'),'--manifests',str(bundle/'manifests')],'generate');rendered=out/'generated.yaml';rendered.write_bytes(generated)
    objects=json_objects(call(k+['create','--dry-run=client','--validate=false','-f',str(rendered),'-o','json'],'parse-generated'))
    summary=[]
    for value in objects:
        meta=value['metadata'];kind=value['kind'];namespace=meta.get('namespace');name=meta['name']
        if kind=='Namespace':assert name=='iot-mesh-system'
        if namespace:assert namespace=='iot-mesh-system',(kind,name,namespace)
        if kind=='Service':assert value['spec'].get('type','ClusterIP')=='ClusterIP' and not value['spec'].get('externalIPs')
        if kind=='Deployment':assert not value['spec']['template']['spec'].get('hostNetwork',False)
        summary.append({'kind':kind,'name':name,'namespace':namespace})
    (out/'render-summary.json').write_text(json.dumps({'manifest_sha256':sha256(generated).hexdigest(),'resources':summary,'source_files':{n:sha256((ROOT/n).read_bytes()).hexdigest() for n in ['scripts/install-private-mesh.py','infra/mesh/assets.json','infra/mesh/istio-operator.yaml']}},indent=2)+'\n')
    call([str(cli),'install','-y','-f',str(ROOT/'infra/mesh/istio-operator.yaml'),'--manifests',str(bundle/'manifests')],'install',480)
    call(k+['rollout','status','deployment/istiod-iot-1313','-n','iot-mesh-system','--timeout=180s'],'pilot-ready',190)
    after=get('get','namespaces');existing={n['metadata']['name']:n['metadata'].get('labels',{}) for n in after['items'] if n['metadata']['name'] in names};assert existing=={n['metadata']['name']:n['metadata'].get('labels',{}) for n in before['items']}
    (out/'namespaces-after.json').write_text(json.dumps(after,indent=2)+'\n')
    for name,value in [('deployment',get('get','deployment','istiod-iot-1313','-n','iot-mesh-system')),('pods',get('get','pods','-n','iot-mesh-system')),('services',get('get','service','-n','iot-mesh-system'))]:(out/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
    proof={'ready':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'version':pins['version'],'revision':pins['revision'],'existing_namespace_labels_unchanged':True,'private_only':True,'rendered_resources':len(summary),'scope':'Minimal control plane ready. Actual workload mTLS/principal authorization tests follow separately; existing serving/feature namespaces remain uninjected.'};(out/'ready.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);run(p.parse_args())
