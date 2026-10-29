"""Install a source-bound read-only GPU host exporter through the existing private cluster without adding a host port or cloud firewall."""
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from pathlib import Path
import argparse,ipaddress,json,os,re,subprocess
ROOT=Path(__file__).resolve().parents[1]


def run(args):
    """Require an actual worker import and native Helm checks while retaining the running model specifications."""
    os.umask(0o077);root=args.runtime_root.resolve();out=args.output.resolve()
    assert os.geteuid()==0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root/'outputs')
    assert re.fullmatch(r'gpu-host-exporter-[a-z0-9-]+',out.name)
    seal=json.loads((ROOT.parent/'manifest.json').read_bytes());assert seal['format']=='iot-gpu-host-telemetry-source-v1'
    for n,pin in seal['files'].items():assert sha256((ROOT/n).read_bytes()).hexdigest()==pin
    assert sha256(args.metadata.read_bytes()).hexdigest()==args.metadata_sha256 and sha256(args.import_receipt.read_bytes()).hexdigest()==args.receipt_sha256
    meta=json.loads(args.metadata.read_bytes());receipt=json.loads(args.import_receipt.read_bytes())
    assert receipt['passed'] and receipt['source_commit']==meta['source_commit']==seal['commit']
    assert receipt['image_ref']==meta['image_ref'] and receipt['config_digest']==meta['config_digest'] and not receipt['workload_changed']
    session=json.loads((root/'data/runtime/ci/session.json').read_bytes())
    assert datetime.fromisoformat(session['auto_stop_utc'])>datetime.now(timezone.utc)+timedelta(minutes=20)
    out.mkdir(exist_ok=False);k=['/usr/local/bin/k3s','kubectl'];name='iot-gpu-host-telemetry';ns='iot-llm'
    def save(n,value):
        """Retain public cluster/import facts without any authentication credential."""
        (out/n).write_text(json.dumps(value,indent=2)+'\n')
    def command(label,argv,timeout=60):
        """Retain independent native command outputs and errors before evaluating readiness."""
        p=subprocess.run(argv,capture_output=True,timeout=timeout)
        (out/(label+'.stdout')).write_bytes(p.stdout);(out/(label+'.stderr')).write_bytes(p.stderr);(out/(label+'.exit')).write_text(str(p.returncode)+'\n')
        if p.returncode:raise RuntimeError(label+' failed')
        return p.stdout
    def get(*query):
        """Read named native resources with the existing private administrative connection."""
        return json.loads(subprocess.check_output(k+['get',*query,'-o','json'],stderr=subprocess.PIPE,timeout=30))
    for kind in ['deployment','networkpolicy']:
        assert not subprocess.check_output(k+['get',kind,name,'-n',ns,'--ignore-not-found','-o','name'],stderr=subprocess.PIPE).strip()
    before=get('deployment','iot-model-gpu','-n',ns);gpu=get('node','iot-cw-gpu');cpu=get('node','iot-cw-vm')
    save('source.json',seal);save('image-metadata.json',meta);save('worker-import.json',receipt);save('model-before.json',before)
    save('gpu-node-before.json',gpu);save('control-node.json',cpu)
    subnet=ipaddress.ip_network(gpu['spec']['podCIDR']);target=str(subnet.network_address+11)
    route=json.loads(command('source-route',['ip','-j','route','get',target]))[0]
    primary=next(v['address'] for v in cpu['status']['addresses'] if v['type']=='InternalIP');source=route.get('prefsrc',route.get('src'))
    assert ipaddress.ip_address(primary).is_private and ipaddress.ip_address(source).is_private
    assert source==primary or ipaddress.ip_address(source) in ipaddress.ip_network(cpu['spec']['podCIDR'])
    values={'image':meta['image_ref'],'sourceCommit':seal['commit'],'gpuNode':'iot-cw-gpu','controlSourceAddresses':sorted({v+'/32' for v in [primary,source]})}
    save('values.json',values);helm=str(root/'data/runtime/kubernetes-assets-27/helm/helm');chart=str(ROOT/'infra/charts/iot-gpu-host-telemetry')
    command('lint',[helm,'lint',chart,'-f',str(out/'values.json')])
    command('render',[helm,'template',name,chart,'-n',ns,'-f',str(out/'values.json')])
    command('server-dry-run',k+['apply','--dry-run=server','-n',ns,'-f',str(out/'render.stdout')])
    command('install',[helm,'--kubeconfig','/etc/rancher/k3s/k3s.yaml','install',name,chart,'-n',ns,'-f',str(out/'values.json'),
        '--atomic','--wait','--timeout=180s'],timeout=210)
    deployment=get('deployment',name,'-n',ns);pods=get('pods','-n',ns,'-l','app.kubernetes.io/instance='+name)
    save('deployment.json',deployment);save('pods.json',pods);save('network-policy.json',get('networkpolicy',name,'-n',ns))
    assert deployment['status']['readyReplicas']==1 and len(pods['items'])==1
    pod=pods['items'][0];assert pod['spec']['nodeName']=='iot-cw-gpu' and not pod['spec'].get('hostNetwork') and not pod['spec'].get('hostPID')
    assert all(c['ready'] for c in pod['status']['containerStatuses'])
    current=get('deployment','iot-model-gpu','-n',ns);save('model-after.json',current)
    assert current['metadata']['uid']==before['metadata']['uid'] and current['spec']==before['spec']
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':seal['commit'],'image_ref':meta['image_ref'],
        'exporter_pod_uid':pod['metadata']['uid'],'exporter_pod_ip':pod['status']['podIP'],'source_addresses':values['controlSourceAddresses'],
        'model_spec_unchanged':True,'host_port_added':False,'cloud_firewall_added':False,
        'scope':'Official-binary exporter is Ready in the existing GPU overlay network. Independent host OS comparisons and Prometheus/Grafana acceptance remain separate.'}
    save('verification.json',report);print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output','metadata','import-receipt']:parser.add_argument('--'+name,type=Path,required=True)
    for name in ['metadata-sha256','receipt-sha256']:parser.add_argument('--'+name,required=True)
    run(parser.parse_args())
