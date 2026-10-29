"""Install a separately isolated, source-tested observer canary without changing the active InferencePool or agent configuration."""
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from pathlib import Path
import argparse,base64,json,os,re,subprocess,sys,tarfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from infra.security.vault_client import VaultClient


def run(args):
    """Keep the live route and original workloads intact while authenticating the new image, Secret and private Helm release."""
    os.umask(0o077);root=args.runtime_root.resolve();out=args.output.resolve();quality=args.quality.resolve()
    if os.geteuid()!=0 or not root.is_relative_to('/srv/iot-data') or not all(p.is_relative_to(root/'outputs') for p in [out,quality]):
        raise ValueError('Use sudo and retained project output paths')
    if not re.fullmatch(r'llm-observer-[a-z0-9-]+',out.name):raise ValueError('Require a distinct observer run identity')
    gate=json.loads((quality/'verification.json').read_bytes());assert gate['passed'] and gate['quality']['fresh_mutation_run'] and not gate['routing_changed']
    source=json.loads((quality/'source.json').read_bytes());assert source['commit']==gate['source_commit']
    for name in source['files']:
        if name.startswith('llm_observability/'):
            assert sha256((ROOT/name).read_bytes()).hexdigest()==source['files'][name],'Runtime code differs from the accepted quality gate'
    session=json.loads((root/'data/runtime/ci/session.json').read_bytes())
    assert datetime.fromisoformat(session['auto_stop_utc'])>datetime.now(timezone.utc)+timedelta(minutes=20)
    gpu=json.loads((root/'data/runtime/ci/gpu-session.json').read_bytes())
    assert datetime.fromisoformat(gpu['last_start_utc'])+timedelta(seconds=28800)>datetime.now(timezone.utc)+timedelta(minutes=20)
    mem=next(int(l.split()[1])*1024 for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:'))
    assert mem>4*1024**3 and os.statvfs(root).f_bavail*os.statvfs(root).f_frsize>10_000_000_000
    out.mkdir(exist_ok=False);k=['/usr/local/bin/k3s','kubectl'];release='iot-llm-observer';namespace='iot-llm'
    def keep(name,value):
        """Retain safe source, readiness and public resource facts; never retain a Secret body."""
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    def command(label,values,payload=None,timeout=60):
        """Preserve bounded command stdout and stderr separately without logging credential-bearing stdin."""
        p=subprocess.run(values,input=payload,capture_output=True,timeout=timeout)
        (out/(label+'.stdout')).write_bytes(p.stdout);(out/(label+'.stderr')).write_bytes(p.stderr)
        (out/(label+'.exit')).write_text(str(p.returncode)+'\n')
        if p.returncode:raise RuntimeError('Observer canary phase failed: '+label)
        return p.stdout
    def get(*query):
        """Read only named cluster objects and approved workload lists."""
        return json.loads(subprocess.check_output(k+['get',*query,'-o','json'],stderr=subprocess.PIPE,timeout=30))
    before={name:get(*query) for name,query in {
        'pool':['inferencepool.inference.networking.k8s.io','iot-router','-n',namespace],
        'router':['deployment','iot-router-epp','-n',namespace],
        'control':['deployment','iot-model-control','-n',namespace],
        'gpu':['deployment','iot-model-gpu','-n',namespace],
        'agents':['sandboxagents','-n','iot-agents'],
        'model':['modelconfig','default-model-config','-n','iot-agents']}.items()}
    for name,value in before.items():keep('before-'+name+'.json',value)
    assert before['pool']['spec']['selector']['matchLabels']=={
        'app.kubernetes.io/instance':'iot-model-control','app.kubernetes.io/name':'iot-llm-model-control'}
    assert before['pool']['spec']['targetPorts']==[{'number':8080}]
    assert before['control']['spec']['template']['spec']['containers'][0]['image']==json.loads((ROOT/'infra/llm-observability/runtime.lock.json').read_bytes())['retained_model_control_image']
    for kind,name in [('deployment',release),('service',release),('networkpolicy',release),('secret','iot-llm-observer-client')]:
        if subprocess.check_output(k+['get',kind,name,'-n',namespace,'--ignore-not-found','-o','name'],stderr=subprocess.PIPE).strip():
            raise ValueError('Inspect and preserve the existing named observer resource before another install')
    if not args.role.resolve().is_relative_to(root/'data/runtime') or not args.ca.resolve().is_relative_to(root):
        raise ValueError('Keep the Vault identity private and its verified public CA inside the retained project')
    client=VaultClient('https://10.52.0.1:18200',args.ca,json.loads(args.role.read_bytes()))
    lease=client.login()
    try:data,version=client.read('iot/data/agents/services')
    finally:client.revoke()
    assert lease['token_policies']==['iot-agent-services'] and lease['lease_duration']==300
    selected=json.loads(data['iot-local-model-client']);assert set(selected)=={'API_KEY'} and len(selected['API_KEY'])>=32
    actual=get('secret','iot-local-model-client','-n','iot-agents')
    assert base64.b64decode(actual['data']['API_KEY']).decode()==selected['API_KEY']
    keep('vault-consumer.json',{'policy':'iot-agent-services','lease':lease,'path':'iot/data/agents/services','version':version,
        'original_secret_uid':actual['metadata']['uid'],'same_credential_bytes':True,'token_revoked':True})
    image=gate['images']['runtime'];actual=json.loads(command('image-before',['docker','image','inspect',image['tag']]))[0]
    assert actual['Id']==image['image_id'] and actual['Config']['Labels']['org.opencontainers.image.revision']==gate['source_commit']
    archive=out/'observer-image.tar';command('image-save',['docker','image','save','-o',str(archive),image['tag']],timeout=300)
    blobs={};manifest=None
    with archive.open('rb') as stream,tarfile.open(fileobj=stream,mode='r|') as saved:
        for item in saved:
            if item.isfile() and item.size<1_000_000:
                body=saved.extractfile(item).read();blobs[item.name]=body
                if item.name=='manifest.json':manifest=json.loads(body)
    assert isinstance(manifest,list) and len(manifest)==1 and manifest[0]['Config'] in blobs
    config=blobs[manifest[0]['Config']];config_digest='sha256:'+sha256(config).hexdigest()
    assert json.loads(config)['config']['Labels']['org.opencontainers.image.revision']==gate['source_commit']
    (out/'image-save-manifest.json').write_bytes(blobs['manifest.json']);(out/'image-save-config.json').write_bytes(config)
    archive_digest=sha256()
    with archive.open('rb') as stream:
        while chunk:=stream.read(1048576):archive_digest.update(chunk)
    keep('image-export.json',{'bytes':archive.stat().st_size,'sha256':archive_digest.hexdigest(),'config_digest':config_digest})
    command('image-import',['/usr/local/bin/k3s','ctr','images','import',str(archive)],timeout=300)
    imported=json.loads(command('image-cri',['/usr/local/bin/k3s','crictl','inspecti',image['tag']]))
    assert imported['status']['id']==config_digest
    choices=[n for n in imported['status']['repoDigests'] if re.fullmatch(r'docker.io/library/iot-llm-observer@sha256:[0-9a-f]{64}',n)]
    assert len(choices)==1
    secret={'apiVersion':'v1','kind':'Secret','metadata':{'name':'iot-llm-observer-client','namespace':namespace},'type':'Opaque','stringData':selected}
    command('secret-create',k+['create','-f','-'],json.dumps(secret).encode())
    secret=get('secret','iot-llm-observer-client','-n',namespace)
    assert base64.b64decode(secret['data']['API_KEY']).decode()==selected['API_KEY']
    keep('secret-identity.json',{'uid':secret['metadata']['uid'],'type':secret['type'],'keys':sorted(secret['data']),'same_credential_bytes':True})
    directory=root/'data/runtime'/out.name/'db';directory.mkdir(parents=True,exist_ok=False);os.chown(directory,10001,10001);directory.chmod(0o750)
    # The private parent's traversal permission must let the non-root Pod reach its own ledger.
    directory.parent.chmod(0o755)
    values={'image':choices[0],'modelControlImage':before['control']['spec']['template']['spec']['containers'][0]['image'],
            'sourceCommit':gate['source_commit'],'clientSecret':'iot-llm-observer-client','dataPath':str(directory),'cpuNode':'iot-cw-vm'}
    keep('values.json',values);chart=ROOT/'infra/charts/iot-llm-observer';helm=root/'data/runtime/kubernetes-assets-27/helm/helm'
    keep('chart-source.json',{str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in sorted(chart.rglob('*')) if p.is_file()})
    command('helm-lint',[str(helm),'lint',str(chart),'-f',str(out/'values.json')])
    command('helm-template',[str(helm),'template',release,str(chart),'-n',namespace,'-f',str(out/'values.json')])
    command('server-dry-run',k+['apply','--dry-run=server','-f',str(out/'helm-template.stdout')])
    command('helm-install',[str(helm),'install',release,str(chart),'-n',namespace,'-f',str(out/'values.json'),'--atomic','--wait','--timeout','240s'],timeout=270)
    after=get('deployment',release,'-n',namespace);keep('deployment.json',after)
    pods=get('pods','-n',namespace,'-l','app.kubernetes.io/instance='+release);keep('pods.json',pods)
    assert after['status']['readyReplicas']==1 and len(pods['items'])==1
    pod=pods['items'][0];assert all(c['ready'] and c.get('state',{}).get('running') for c in pod['status']['containerStatuses'])
    snippet='from pathlib import Path; from hashlib import sha256; import json; print(json.dumps({n:sha256((Path("/workspace")/n).read_bytes()).hexdigest() for n in '+repr(sorted(image['metadata']['files']))+'}))'
    packaged=json.loads(command('packaged-files',k+['exec','-n',namespace,pod['metadata']['name'],'-c','observer','--','python','-B','-c',snippet]))
    assert packaged==image['metadata']['files']
    keep('service.json',get('service',release,'-n',namespace));keep('network-policy.json',get('networkpolicy',release,'-n',namespace))
    for name,previous in before.items():
        query={'pool':['inferencepool.inference.networking.k8s.io','iot-router','-n',namespace],
            'router':['deployment','iot-router-epp','-n',namespace],'control':['deployment','iot-model-control','-n',namespace],
            'gpu':['deployment','iot-model-gpu','-n',namespace],'agents':['sandboxagents','-n','iot-agents'],
            'model':['modelconfig','default-model-config','-n','iot-agents']}[name]
        current=get(*query);keep('after-'+name+'.json',current)
        if name=='agents':assert {a['metadata']['uid']:a['spec'] for a in current['items']}=={a['metadata']['uid']:a['spec'] for a in previous['items']}
        else:assert current['metadata']['uid']==previous['metadata']['uid'] and current['spec']==previous['spec']
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'tested_source_commit':gate['source_commit'],
        'image':choices[0],'config_digest':config_digest,'pod_uid':pod['metadata']['uid'],'packaged_files':packaged,'routing_changed':False,
        'scope':'A separate private observer canary is ready. Original active routing, adapter, GPU, agent and model specifications are unchanged. Real canary requests and agent telemetry promotion still require separate verification.'}
    keep('verification.json',report)
    variants=[selected['API_KEY'].encode(),base64.b64encode(selected['API_KEY'].encode())]
    for p in out.iterdir():
        if p.is_file() and p.suffix!='.tar':assert not any(v in p.read_bytes() for v in variants),'Credential-bearing canary output refused'
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output','quality','role','ca']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
