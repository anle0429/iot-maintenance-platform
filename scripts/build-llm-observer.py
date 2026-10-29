"""Build source-bound observer images and execute a fresh bounded native quality gate without changing live routing."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import argparse,json,os,re,subprocess

ROOT=Path(__file__).resolve().parents[1]
RUNTIME_FILES={'llm_observability/'+n+'.py' for n in ['__init__','contracts','ledger','metrics','private_traces','app']}


def run(args):
    """Preserve actual parent identities, packaged bytes, environment and terminal test-container state for review."""
    root=args.runtime_root.resolve();out=args.output.resolve()
    if os.geteuid()!=0 or not root.is_relative_to('/srv/iot-data') or not out.is_relative_to(root/'outputs') or not re.fullmatch(r'llm-observer-[a-z0-9-]+',out.name):
        raise ValueError('Use sudo and a new retained lab observer output')
    seal=json.loads((ROOT.parent/'manifest.json').read_bytes());assert seal['format']=='iot-llm-observer-source-v1'
    for name,digest in seal['files'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    session=json.loads((root/'data/runtime/ci/session.json').read_bytes())
    if (datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc)).total_seconds()<8400:raise ValueError('Require sufficient actual CPU session time for the bounded fresh quality gate')
    out.mkdir(parents=True,exist_ok=False);lock=json.loads((ROOT/'infra/llm-observability/runtime.lock.json').read_bytes());images={}
    def keep(name,value):
        """Retain native identity/phase results without collecting runtime Secrets or live request bodies."""
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    def command(name,values,timeout=600):
        """Store exact bounded native stdout/stderr and exit state in separate original files."""
        with (out/(name+'.stdout')).open('xb') as stdout,(out/(name+'.stderr')).open('xb') as stderr:
            result=subprocess.run(values,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=timeout)
        (out/(name+'.exit')).write_text(str(result.returncode)+'\n')
        if result.returncode:raise RuntimeError('Native observer phase failed: '+name)
        return (out/(name+'.stdout')).read_bytes()
    keep('source.json',seal);keep('cpu-session.json',session)
    for kind,dockerfile in [('runtime','Runtime.Dockerfile'),('test','Tests.Dockerfile')]:
        parent=lock['runtime_parent' if kind=='runtime' else 'test_parent'];actual=json.loads(command(kind+'-parent',['docker','image','inspect',parent['image']]))[0]
        assert actual['Id']==parent['image_id'],'Preserve a different dependency image'
        tag='iot-llm-observer'+('-tests' if kind=='test' else '')+':'+seal['commit'][:12]
        command(kind+'-build',['docker','build','--label','org.opencontainers.image.revision='+seal['commit'],
                '--build-arg','BASE_IMAGE='+parent['image'],'-f','infra/llm-observability/'+dockerfile,'-t',tag,'.'],600)
        actual=json.loads(command(kind+'-image',['docker','image','inspect',tag]))[0]
        assert actual['Config']['Labels']['org.opencontainers.image.revision']==seal['commit']
        names=['fastapi','starlette','pydantic','httpx','httpcore','h11','prometheus-client','opentelemetry-sdk','opentelemetry-proto','protobuf','uvicorn']
        snippet='from pathlib import Path; from hashlib import sha256; import importlib.metadata as m,json; '+\
                'print(json.dumps({"packages":{n:m.version(n) for n in '+repr(names)+'},"files":{n:sha256((Path("/workspace")/n).read_bytes()).hexdigest() for n in '+repr(sorted(RUNTIME_FILES))+'}}))'
        if kind=='runtime':
            metadata=json.loads(command(kind+'-packaged',['docker','run','--rm','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
                '--memory=512m','--cpus=1','--entrypoint=python',tag,'-B','-c',snippet]))
            assert metadata['files']=={n:seal['files'][n] for n in RUNTIME_FILES}
            command('runtime-freeze',['docker','run','--rm','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
                '--memory=512m','--cpus=1','--entrypoint=python',tag,'-m','pip','freeze'])
        else:
            snippet='import importlib.metadata as m,json; print(json.dumps({"packages":{n:m.version(n) for n in '+repr(names)+'}}))'
            metadata=json.loads(command(kind+'-packaged',['docker','run','--rm','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
                '--memory=512m','--cpus=1','--entrypoint=python',tag,'-B','-c',snippet]))
        images[kind]={'tag':tag,'image_id':actual['Id'],'parent':parent,'metadata':metadata}
    assert images['runtime']['metadata']['packages']==images['test']['metadata']['packages'],'Test/runtime dependency versions differ'
    keep('images.json',images)
    work=out/'quality-work';proof=out/'quality-proof';work.mkdir();proof.mkdir()
    for path in [work,proof]:os.chown(path,10001,10001);path.chmod(0o750)
    container='iot-'+out.name+'-quality';owned=subprocess.run(['docker','container','inspect',container],capture_output=True)
    if owned.returncode==0:raise ValueError('Preserve the existing quality process; inspect its terminal state before another run')
    try:
        command('quality',['docker','run','--name',container,'--label','iot.coursework/observer-run='+out.name,'--label','iot.coursework/source='+seal['commit'],
            '--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=512','--memory=6g','--cpus=5',
            '--tmpfs','/tmp:rw,nosuid,nodev,size=512m',
            '--mount','type=bind,src='+str(ROOT.parent)+',dst=/source,readonly',
            '--mount','type=bind,src='+str(work)+',dst=/work',
            '--mount','type=bind,src='+str(proof)+',dst=/proof',
            images['test']['tag'],'-B','/source/tree/scripts/test-llm-observer.py','--source','/source/tree','--manifest','/source/manifest.json',
            '--workspace','/work/run','--output','/proof/run'],7400)
    finally:
        state=subprocess.run(['docker','container','inspect',container],capture_output=True)
        if state.returncode==0:
            native=json.loads(state.stdout)[0];assert native['Config']['Labels']['iot.coursework/observer-run']==out.name
            if native['State']['Running']:
                subprocess.run(['docker','stop','--time=10',container],capture_output=True,timeout=30)
                native=json.loads(subprocess.check_output(['docker','container','inspect',container]))[0]
            keep('quality-container.json',native)
    gate=json.loads((proof/'run/verification.json').read_bytes());assert gate['passed'] and gate['commit']==seal['commit']
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':seal['commit'],'images':images,
            'quality':gate,'routing_changed':False,'scope':'Fresh complete observer quality gate and source-tested runtime images. No canary install or traffic promotion.'}
    keep('verification.json',report);print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
