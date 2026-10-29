"""Package the already authenticated official host exporter without downloading dependencies or changing a running service."""
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from pathlib import Path
import argparse,json,os,re,shutil,subprocess,tarfile
ROOT=Path(__file__).resolve().parents[1]


def digest(path):
    """Hash retained archives incrementally rather than loading image layers into memory."""
    result=sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1048576),b''):result.update(chunk)
    return result.hexdigest()


def run(args):
    """Retain official archive, packaged binary and immutable image identities; deployment is a separate operation."""
    os.umask(0o077);root=args.runtime_root.resolve();out=args.output.resolve()
    assert os.geteuid()==0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root/'outputs')
    assert re.fullmatch(r'gpu-host-exporter-[a-z0-9-]+',out.name)
    source=json.loads((ROOT.parent/'manifest.json').read_bytes());assert source['format']=='iot-gpu-host-telemetry-source-v1'
    for n,pin in source['files'].items():assert digest(ROOT/n)==pin,n
    session=json.loads((root/'data/runtime/ci/session.json').read_bytes())
    assert datetime.fromisoformat(session['auto_stop_utc'])>datetime.now(timezone.utc)+timedelta(minutes=20)
    assert shutil.disk_usage(root).free>2_000_000_000
    out.mkdir(exist_ok=False)
    def save(name,value):
        """Keep safe original build and source facts separate from large local image layers."""
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    def command(label,argv,timeout=120):
        """Keep every bounded process outcome with its original standard output and error."""
        p=subprocess.run(argv,capture_output=True,timeout=timeout)
        (out/(label+'.stdout')).write_bytes(p.stdout);(out/(label+'.stderr')).write_bytes(p.stderr);(out/(label+'.exit')).write_text(str(p.returncode)+'\n')
        if p.returncode:raise RuntimeError(label+' failed')
        return p.stdout
    save('source.json',source);lock=json.loads((ROOT/'infra/llm-observability/host-exporter.lock.json').read_bytes())
    assets=root/'data/runtime/monitoring-assets-28'
    candidates=[p for p in assets.rglob('*') if p.is_file() and p.stat().st_size==lock['archive_bytes'] and digest(p)==lock['archive_sha256']]
    assert len(candidates)==1,'Require the unchanged authenticated official archive from the existing host lab'
    context=out/'context';context.mkdir()
    with tarfile.open(candidates[0]) as package:
        binary=package.extractfile(lock['member']).read();assert len(binary)==lock['binary_bytes'] and sha256(binary).hexdigest()==lock['binary_sha256']
        (context/'node_exporter').write_bytes(binary)
        prefix=lock['member'].rsplit('/',1)[0]
        for name in ['LICENSE','NOTICE']:(context/name).write_bytes(package.extractfile(prefix+'/'+name).read())
    (context/'node_exporter').chmod(0o555)
    shutil.copyfile(ROOT/'infra/llm-observability/HostExporter.Dockerfile',context/'Dockerfile')
    save('upstream.json',lock|{'retained_archive':str(candidates[0]),'downloaded_again':False})
    tag='iot-gpu-host-telemetry:'+source['commit'][:12]
    command('build',['docker','build','--network=none','--label','org.opencontainers.image.revision='+source['commit'],
        '--label','org.opencontainers.image.source=https://github.com/prometheus/node_exporter','-t',tag,str(context)],timeout=180)
    image=json.loads(command('image',['docker','image','inspect',tag]))[0]
    assert image['Config']['User']=='65534:65534' and image['Os']=='linux' and image['Architecture']=='amd64'
    version=command('version',['docker','run','--rm','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
        '--memory=64m','--cpus=.25',tag,'--version']).decode()
    assert 'version '+lock['version'] in version and lock['upstream_revision'] in version
    cid=command('create',['docker','create',tag]).decode().strip();assert re.fullmatch(r'[a-f0-9]{64}',cid)
    command('copy-packaged-binary',['docker','cp',cid+':/node_exporter',str(out/'packaged-node-exporter')])
    assert digest(out/'packaged-node-exporter')==lock['binary_sha256']
    command('remove-unused-build-container',['docker','rm',cid])
    archive=out/'image.tar';command('save-image',['docker','image','save','-o',str(archive),tag])
    blobs={}
    with archive.open('rb') as stream,tarfile.open(fileobj=stream,mode='r|') as package:
        for member in package:
            if member.isfile() and member.size<1_000_000:blobs[member.name]=package.extractfile(member).read()
    index=json.loads(blobs['index.json']);selected=[v for v in index['manifests'] if v.get('annotations',{}).get('io.containerd.image.name')=='docker.io/library/'+tag]
    assert len(selected)==1;index_digest=selected[0]['digest'];index_blob=blobs['blobs/sha256/'+index_digest.split(':')[1]]
    assert 'sha256:'+sha256(index_blob).hexdigest()==index_digest
    manifest=json.loads(blobs['manifest.json']);assert len(manifest)==1
    config=blobs[manifest[0]['Config']];config_digest='sha256:'+sha256(config).hexdigest()
    (out/'image-save-index.json').write_bytes(blobs['index.json']);(out/'image-oci-index.json').write_bytes(index_blob);(out/'image-save-config.json').write_bytes(config)
    export={'format':'iot-native-gpu-host-image-v1','source_commit':source['commit'],'image_tag':tag,
        'image_ref':'docker.io/library/iot-gpu-host-telemetry@'+index_digest,'index_digest':index_digest,'config_digest':config_digest,
        'archive_sha256':digest(archive),'archive_bytes':archive.stat().st_size,'binary_sha256':lock['binary_sha256'],'binary_bytes':lock['binary_bytes']}
    save('export.json',export)
    public=Path('/tmp')/('iot-gpu-host-exporter-'+source['commit'][:12]+'.tar')
    with public.open('xb') as destination,archive.open('rb') as incoming:shutil.copyfileobj(incoming,destination)
    public.chmod(0o644);public_meta=public.with_suffix('.json')
    with public_meta.open('xb') as destination:destination.write((out/'export.json').read_bytes())
    public_meta.chmod(0o644)
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':source['commit'],
        'official_binary_packaged_exactly':True,'export':export,'public_archive':str(public),'public_metadata':str(public_meta),
        'metadata_sha256':digest(public_meta),'resources_deployed':False}
    save('verification.json',report);print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
