"""Import only the authenticated minimal host-exporter image on the existing GPU worker without changing its model deployment."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import argparse,json,os,re,subprocess,tarfile


def digest(path):
    """Hash the small native image incrementally."""
    value=sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''):value.update(block)
    return value.hexdigest()


def run(args):
    """Authenticate the CPU-built image packet before registering its exact immutable reference in the GPU image store."""
    os.umask(0o077);out=args.output.resolve();assert os.geteuid()==0 and out.is_relative_to('/srv/iot-inference/outputs')
    assert re.fullmatch(r'gpu-host-exporter-[a-z0-9-]+',out.name)
    assert digest(args.metadata)==args.metadata_sha256
    meta=json.loads(args.metadata.read_bytes());assert meta['format']=='iot-native-gpu-host-image-v1'
    assert re.fullmatch(r'docker.io/library/iot-gpu-host-telemetry@sha256:[a-f0-9]{64}',meta['image_ref'])
    assert meta['binary_sha256']=='1108f7453ecfe4a72f131c73b69537c171840ad4f1713a2402328ad56cf12e09'
    assert args.image.stat().st_size==meta['archive_bytes']<40_000_000 and digest(args.image)==meta['archive_sha256']
    out.mkdir(parents=True,exist_ok=False);blobs={}
    with args.image.open('rb') as stream,tarfile.open(fileobj=stream,mode='r|') as archive:
        for member in archive:
            if member.isfile() and member.size<1_000_000:blobs[member.name]=archive.extractfile(member).read()
    manifest=json.loads(blobs['manifest.json']);assert len(manifest)==1;config=blobs[manifest[0]['Config']]
    assert 'sha256:'+sha256(config).hexdigest()==meta['config_digest']
    assert json.loads(config)['config']['Labels']['org.opencontainers.image.revision']==meta['source_commit']
    index=blobs['blobs/sha256/'+meta['index_digest'].split(':')[1]];assert 'sha256:'+sha256(index).hexdigest()==meta['index_digest']
    def command(label,argv):
        """Retain the original bounded import and readback outcomes."""
        p=subprocess.run(argv,capture_output=True,timeout=120)
        (out/(label+'.stdout')).write_bytes(p.stdout);(out/(label+'.stderr')).write_bytes(p.stderr);(out/(label+'.exit')).write_text(str(p.returncode)+'\n')
        if p.returncode:raise RuntimeError(label+' failed')
        return p.stdout
    command('import',['/usr/local/bin/k3s','ctr','images','import','--digests','--base-name','docker.io/library/iot-gpu-host-telemetry',str(args.image)])
    old=subprocess.run(['/usr/local/bin/k3s','crictl','inspecti',meta['image_ref']],capture_output=True,timeout=30)
    (out/'existing-reference.stdout').write_bytes(old.stdout);(out/'existing-reference.stderr').write_bytes(old.stderr)
    if old.returncode==0:assert json.loads(old.stdout)['status']['id']==meta['config_digest']
    else:command('reference',['/usr/local/bin/k3s','ctr','images','tag','docker.io/library/'+meta['image_tag'],meta['image_ref']])
    readback=json.loads(command('readback',['/usr/local/bin/k3s','crictl','inspecti',meta['image_ref']]))
    assert readback['status']['id']==meta['config_digest'] and meta['image_ref'] in readback['status']['repoDigests']
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':meta['source_commit'],'image_ref':meta['image_ref'],
        'config_digest':meta['config_digest'],'archive_sha256':meta['archive_sha256'],'metadata_sha256':args.metadata_sha256,'workload_changed':False}
    public=Path('/tmp')/('iot-gpu-host-import-'+meta['source_commit'][:12]+'.json')
    with public.open('xb') as stream:stream.write((json.dumps(report,indent=2)+'\n').encode())
    public.chmod(0o644);(out/'verification.json').write_bytes(public.read_bytes());print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['image','metadata','output']:parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--metadata-sha256',required=True);run(parser.parse_args())
