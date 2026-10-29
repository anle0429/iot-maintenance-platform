"""Retain original GPU worker-join receipts after scanning the actual limited join credential."""
from pathlib import Path
from hashlib import sha256
import json,subprocess,tarfile,base64
root=Path('/srv/iot-inference'); out=root/'outputs/gpu-node-capture-29a';out.mkdir(exist_ok=False)
secret=(root/'private/agent-token').read_bytes().strip()
private=[secret,base64.b64encode(secret)]
def keep(name,body):
 assert not any(s in body for s in private), name
 p=out/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(body)
unit='iot-gpu-node-29a'
keep('process/unit.txt',subprocess.check_output(['systemctl','show',unit,'--property=Id,ActiveState,Result,ExecMainStatus,InvocationID,ExecStart']))
keep('process/journal.jsonl',subprocess.check_output(['journalctl','-u',unit,'--no-pager','-o','json']))
for label,base in [('native',root/'outputs/gpu-node-29a'),('source',root/'source/gpu-node-29a')]:
 for p in sorted(base.rglob('*')):
  if p.is_file():
   assert not p.is_symlink();keep(label+'/'+p.relative_to(base).as_posix(),p.read_bytes())
for name,args in [
 ('agent-service.txt',['systemctl','show','k3s-agent','--property=ActiveState,SubState,MainPID']),
 ('agent-config.yaml',['cat','/etc/rancher/k3s/config.yaml']),
 ('gpu.csv',['nvidia-smi','--query-gpu=name,uuid,driver_version,memory.total','--format=csv,noheader']),
 ('join-credential-mode.txt',['stat','--format=%a:%U:%G',str(root/'private/agent-token')])]:
 keep('readback/'+name,subprocess.check_output(args))
keep('capture-operator.py',Path(__file__).read_bytes())
files={p.relative_to(out).as_posix():{'bytes':p.stat().st_size,'sha256':sha256(p.read_bytes()).hexdigest()} for p in sorted(out.rglob('*')) if p.is_file()}
keep('inventory.json',(json.dumps({'files':files},indent=2)+'\n').encode())
archive=root/'outputs/gpu-node-capture-29a.tar'
with tarfile.open(archive,'w') as t:t.add(out,arcname='gpu-node-capture-29a')
print(json.dumps({'files':len(files),'bytes':archive.stat().st_size,'sha256':sha256(archive.read_bytes()).hexdigest()}))
