"""Extend the retained private Alloy/Grafana installation with source-controlled LLM views and approved request logs."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import Request,build_opener,ProxyHandler
import argparse,json,os,subprocess,time

ROOT=Path(__file__).resolve().parents[1]


def run(args):
    """Validate before reload, retain all original pipelines and dashboards, and verify the running viewers without restarting them."""
    os.umask(0o077);root=args.runtime_root.resolve();out=args.output.resolve()
    assert os.geteuid()==0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root/'outputs')
    source=json.loads((ROOT.parent/'manifest.json').read_bytes());assert source['format']=='iot-llm-dashboards-source-v1'
    for name,digest in source['files'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==digest
    out.mkdir(exist_ok=False);opener=build_opener(ProxyHandler({}))
    def save(name,value):
        """Persist public source, command and query receipts only."""
        (out/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    def fetch(name,url,method='GET'):
        """Keep original bounded private viewer responses without copying request authentication headers."""
        with opener.open(Request(url,method=method),timeout=30) as response:
            raw=response.read(8*1024**2+1);assert response.status==200 and len(raw)<=8*1024**2
        (out/name).write_bytes(raw);return raw
    def command(name,argv):
        """Preserve original native validation and reload outcomes."""
        result=subprocess.run(argv,capture_output=True,timeout=60)
        (out/(name+'.stdout')).write_bytes(result.stdout);(out/(name+'.stderr')).write_bytes(result.stderr)
        save(name+'-exit.json',{'exit_code':result.returncode});assert result.returncode==0,name
        return result.stdout
    save('source.json',source)
    runtime=root/'data/runtime/log-discovery-28';config=runtime/'config.alloy';old=config.read_bytes()
    token=root/'data/runtime/llm-monitoring-29a/auth/token';assert token.is_file() and token.stat().st_mode&0o777==0o640
    template=(ROOT/'infra/llm-observability/observer-logs.alloy').read_text()
    assert 'discovery.kubernetes "llm_observer"' not in old.decode()
    addition=template.replace('@AUTH@',str(token.parent));assert '@AUTH@' not in addition
    planned=old+b'\n'+addition.encode();(out/'alloy-before.alloy').write_bytes(old);(out/'alloy-planned.alloy').write_bytes(planned)
    alloy=root/'data/runtime/logging-assets-28/alloy-linux-amd64'
    command('alloy-version',[str(alloy),'--version'])
    command('alloy-validate',[str(alloy),'validate',str(out/'alloy-planned.alloy')])
    unit='iot-alloy-discovery-28';pid=int(command('alloy-pid-before',['systemctl','show',unit,'-p','MainPID','--value']))
    grafana_pid=int(command('grafana-pid-before',['systemctl','show','iot-grafana-28','-p','MainPID','--value']))
    assert pid>0 and grafana_pid>0 and config.read_bytes()==old
    destination=root/'data/runtime/monitoring-28/dashboards';assert destination.is_dir()
    expected={}
    for name in ['runtime','gpu-host','requests']:
        raw=(ROOT/('infra/llm-observability/dashboards/'+name+'.json')).read_bytes();asset=json.loads(raw)
        target=destination/('llm-'+name+'.json');assert not target.exists()
        expected[asset['uid']]=asset;target.write_bytes(raw);target.chmod(0o644)
    pending=config.with_name('llm-observer.pending');pending.write_bytes(planned);pending.chmod(0o644);os.replace(pending,config)
    fetch('alloy-reload.txt','http://127.0.0.1:12345/-/reload','POST')
    observations=[]
    for attempt in range(20):
        listed=json.loads(fetch('dashboard-list-'+str(attempt)+'.json','http://127.0.0.1:13001/api/search?type=dash-db'))
        found=set(v['uid'] for v in listed)&set(expected);observations.append({'attempt':attempt,'found':sorted(found)})
        if found==set(expected):break
        time.sleep(2)
    else:raise AssertionError('Retain the original viewer results: new dashboards were not provisioned')
    for uid,asset in expected.items():
        actual=json.loads(fetch('dashboard-'+uid+'.json','http://127.0.0.1:13001/api/dashboards/uid/'+uid))['dashboard']
        assert actual['title']==asset['title'] and actual['panels']==asset['panels']
    assert pid==int(command('alloy-pid-after',['systemctl','show',unit,'-p','MainPID','--value']))
    assert grafana_pid==int(command('grafana-pid-after',['systemctl','show','iot-grafana-28','-p','MainPID','--value']))
    assert config.read_bytes()==planned and planned.startswith(old+b'\n')
    command('alloy-journal',['journalctl','-u',unit,'--since','-3min','--no-pager','-o','short-iso'])
    save('observations.json',observations)
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':source['commit'],
        'dashboards':sorted(expected),'panel_count':sum(len(v['panels']) for v in expected.values()),
        'old_alloy_prefix_preserved':True,'alloy_main_pid':pid,'grafana_main_pid':grafana_pid,
        'scope':'Native configuration validation, no-restart collector reload and exact provisioned dashboard readbacks. Numeric, log, gateway and original UI acceptance are checked separately.'}
    save('verification.json',report);print(json.dumps(report))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
