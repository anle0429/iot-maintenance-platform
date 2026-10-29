"""Run the initialized, baselined official local mutation session without changing its source or selected work."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime,timezone
import json,os,subprocess,time
base=Path(__file__).resolve().parent;worker=base/'worker'
cli=base.parent/'venv-mcp-cosmic/Scripts/cosmic-ray.exe'
assert not (base/'running.json').exists()
preflight=json.loads((base/'pre-execution.json').read_bytes())
assert preflight['baseline_passed'] and preflight['baseline_tests']==132 and preflight['total']==850
seal=json.loads((base/'sealed/manifest.json').read_bytes())
assert all(sha256((worker/name).read_bytes()).hexdigest()==digest for name,digest in seal['files'].items())
os.environ['PYTHONDONTWRITEBYTECODE']='1';os.environ['PYTHONUTF8']='1'
started=datetime.now(timezone.utc).isoformat();clock=time.monotonic()
command=[str(cli),'--verbosity','INFO','exec',str(base/'cosmic-ray.toml'),str(base/'session.sqlite')]
with (base/'exec.stdout').open('wb') as output,(base/'exec.stderr').open('wb') as errors:
 process=subprocess.Popen(command,cwd=worker,stdout=output,stderr=errors,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 (base/'running.json').write_text(json.dumps({'driver_pid':os.getpid(),'process_pid':process.pid,'started_utc':started,'command':command,'worker':str(worker),'scope':'Official Cosmic Ray local distributor, one test process at a time; no source changes or worker restart.'},indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'started_utc':started,'driver_pid':os.getpid(),'process_pid':process.pid,'total_work':850}),flush=True)
 code=process.wait()
restored=all(sha256((worker/name).read_bytes()).hexdigest()==digest for name,digest in seal['files'].items())
with (base/'results.jsonl').open('wb') as output,(base/'export.stderr').open('wb') as errors:
 exported=subprocess.run([str(cli),'dump',str(base/'session.sqlite')],stdout=output,stderr=errors,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
terminal={'process_exit':code,'export_exit':exported.returncode,'finished_utc':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':time.monotonic()-clock,'source_restored':restored,'scope':'Terminal official CLI/export facts only; a separate strict scoreboard must determine mutation acceptance.'}
(base/'terminal.json').write_text(json.dumps(terminal,indent=2)+'\n',encoding='utf-8')
print(json.dumps(terminal),flush=True)
raise SystemExit(0 if code==0 and exported.returncode==0 and restored else 1)
