"""Prepare the already-pinned small model on the existing GPU worker without changing any running inference service."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import Request,build_opener,ProxyHandler
import argparse,json,os,shutil,time


def digest(path):
    """Hash model bytes in fixed chunks without loading weights into host RAM."""
    value=sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''):value.update(block)
    return value.hexdigest()


def run(args):
    """Verify worker identity, existing lock and actual free space before an exclusive, checksum-verified model preparation."""
    os.umask(0o077);root=Path('/srv/iot-inference');assert os.geteuid()==0 and root.is_dir()
    lock=json.loads(args.lock.read_bytes());assert lock['model_id']=='Qwen/Qwen2.5-1.5B-Instruct-GGUF' and lock['served_model']=='iot-qwen2.5-1.5b'
    assert lock['sha256']=='6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e' and lock['bytes']==1117320736
    deadline=datetime.fromisoformat(args.deadline_utc);now=datetime.now(timezone.utc);assert 900<(deadline-now).total_seconds()<=28800
    opener=build_opener(ProxyHandler({}));identity={}
    for name,path in [('project','project/project-id'),('name','instance/name')]:
        with opener.open(Request('http://metadata.google.internal/computeMetadata/v1/'+path,headers={'Metadata-Flavor':'Google'}),timeout=5) as response:identity[name]=response.read().decode()
    assert identity=={'project':'project-208f5905-011e-4ae9-9a5','name':'iot-cw-gpu'}
    free=shutil.disk_usage(root).free;available=int(next(v.split()[1] for v in Path('/proc/meminfo').read_text().splitlines() if v.startswith('MemAvailable:')))*1024
    assert free>=lock['bytes']+4*1024**3 and available>=4*1024**3
    out=args.output.resolve();assert out.is_relative_to(root/'outputs');out.mkdir(exist_ok=False)
    folder=root/'models'/lock['served_model'];folder.mkdir(exist_ok=True);folder.chmod(0o755);target=folder/lock['file'];downloaded=False;started=time.monotonic()
    if not target.exists():
        temporary=folder/(lock['file']+'.partial');url='https://huggingface.co/'+lock['model_id']+'/resolve/'+lock['revision']+'/'+lock['file']
        total=0;last=time.monotonic()
        with opener.open(url,timeout=120) as response,temporary.open('xb') as destination:
            while True:
                block=response.read(1048576)
                if not block:break
                destination.write(block);total+=len(block);assert total<=lock['bytes']
                if time.monotonic()-last>=10:
                    print(json.dumps({'downloaded_bytes':total,'expected_bytes':lock['bytes']}),flush=True);last=time.monotonic()
        assert temporary.stat().st_size==lock['bytes'] and digest(temporary)==lock['sha256']
        os.link(temporary,target);temporary.unlink();target.chmod(0o644);downloaded=True
    assert target.stat().st_size==lock['bytes'] and digest(target)==lock['sha256']
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'identity':identity,'deadline_utc':deadline.isoformat(),
        'free_disk_bytes_before':free,'available_host_memory_bytes_before':available,'lock':lock,'lock_sha256':digest(args.lock),
        'prepared_model':str(target),'model_bytes':target.stat().st_size,'model_sha256':digest(target),'downloaded':downloaded,
        'elapsed_seconds':time.monotonic()-started,'running_services_changed':False,'script_sha256':digest(Path(__file__)),
        'scope':'Prepare an immutable existing model artifact on the already authorized GPU VM. No inference deployment, model comparison, extra VM/disk or public listener.'}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['lock','output']:parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--deadline-utc',required=True);run(parser.parse_args())
