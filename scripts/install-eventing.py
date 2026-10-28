"""Install pinned official Knative Eventing components on the existing private coursework VM with resource/session guards."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import argparse,json,os,shutil,subprocess,urllib.request


def install(root,assets,output):
    """Refuse an existing namespace, preserve original assets and require actual rollout rather than YAML-only proof."""
    root=root.resolve();declaration=json.loads(assets.read_bytes());output=output.resolve()
    if os.geteuid()!=0 or not root.is_relative_to('/srv/iot-data') or not Path('/srv/iot-data').is_mount() or not Path('/srv/iot-fast').is_mount():
        raise ValueError('Use the already approved Linux coursework VM and mounted data disks')
    session=json.loads((root/'data/runtime/ci/session.json').read_bytes());now=datetime.now(timezone.utc)
    if (datetime.fromisoformat(session['auto_stop_utc'])-now).total_seconds()<5400:raise ValueError('Keep90minutes in the approved session')
    ram=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
    if ram<24*1024**3 or min(shutil.disk_usage(root).free,shutil.disk_usage('/srv/iot-fast').free)<20_000_000_000:raise ValueError('Reserve24GiB RAM and20GB free on both data disks')
    k=['/usr/local/bin/k3s','kubectl']
    def get(*args):return json.loads(subprocess.check_output(k+list(args)+['-o','json']))
    version=get('version')['serverVersion']['gitVersion'];major,minor=version.lstrip('v').split('.')[:2]
    if (int(major),int(minor))<(1,35):raise ValueError('This pinned release requires Kubernetes1.35 or newer')
    if subprocess.check_output(k+['get','namespace','knative-eventing','--ignore-not-found','-o','name'],text=True).strip():raise ValueError('Preserve an existing Eventing installation; inspect its version first')
    output.mkdir(parents=True,exist_ok=False);folder=output/'assets';folder.mkdir()
    def save(n,v):(output/n).write_text(json.dumps(v,indent=2)+'\n')
    save('preflight.json',{'checked_utc':now.isoformat(),'session':session,'ram_available_bytes':ram,'kubernetes_version':version,'hdd_free_bytes':shutil.disk_usage(root).free,'ssd_free_bytes':shutil.disk_usage('/srv/iot-fast').free,'source_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),'declaration_sha256':sha256(assets.read_bytes()).hexdigest()})
    for name,entry in declaration['assets'].items():
        if Path(name).name!=name or not entry['url'].startswith('https://github.com/knative/eventing/releases/download/'+declaration['version']+'/'):raise ValueError('Unsafe asset path or source')
        with urllib.request.urlopen(urllib.request.Request(entry['url'],headers={'User-Agent':'iot-coursework-pinned-installer'}),timeout=40) as response:raw=response.read(1_000_001)
        if len(raw)!=entry['bytes'] or sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('Changed upstream asset: '+name)
        (folder/name).write_bytes(raw)
    checksums=(folder/'checksums.txt').read_text()
    for name,entry in declaration['assets'].items():
        if name!='checksums.txt' and entry['sha256'] not in checksums:raise ValueError('Official checksum inventory differs')
    def command(args,name,timeout=240):
        with (output/name).open('x') as stream:subprocess.run(k+args,stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    command(['apply','--server-side','--field-manager=iot-eventing-coursework','-f',str(folder/'eventing-crds.yaml')],'crds-apply.txt')
    command(['wait','--for=condition=Established','crd/brokers.eventing.knative.dev','crd/triggers.eventing.knative.dev','--timeout=120s'],'crds-ready.txt',130)
    command(['apply','--server-side','--field-manager=iot-eventing-coursework','-f',str(folder/'eventing-core.yaml')],'core-apply.txt')
    for name in ['eventing-controller','eventing-webhook','job-sink']:
        command(['rollout','status','deployment/'+name,'-n','knative-eventing','--timeout=240s'],name+'-ready.txt',250)
    for name in ['in-memory-channel','mt-channel-broker']:
        command(['apply','--server-side','--field-manager=iot-eventing-coursework','-f',str(folder/(name+'.yaml'))],name+'-apply.txt')
    for name in ['imc-controller','imc-dispatcher','mt-broker-controller','mt-broker-ingress','mt-broker-filter']:
        command(['rollout','status','deployment/'+name,'-n','knative-eventing','--timeout=240s'],name+'-ready.txt',250)
    services=get('get','services','-n','knative-eventing')
    if any(s['spec'].get('type','ClusterIP')!='ClusterIP' or s['spec'].get('externalIPs') for s in services['items']):raise ValueError('Eventing services must remain private ClusterIP')
    for resource in ['pods','deployments','statefulsets','services']:save(resource+'.json',get('get',resource,'-n','knative-eventing'))
    save('verification.json',{'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'version':declaration['version'],'kubernetes_version':version,'assets':declaration['assets'],'scope':'Actual official Eventing controllers/channel/broker components rolled out on the same VM, private ClusterIP. No application Broker/Trigger/KServe delivery or CI claim yet.'})
    print((output/'verification.json').read_text())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['root','assets','output']:parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();install(args.root,args.assets,args.output)
