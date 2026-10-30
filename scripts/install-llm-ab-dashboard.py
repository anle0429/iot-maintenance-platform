"""Provision a source-controlled comparison view only after a complete native experiment and reconcile its numerical panels."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import build_opener,ProxyHandler
import argparse,json,os,re,subprocess,time
ROOT=Path(__file__).resolve().parents[1]


def run(args):
    """Reuse the private Grafana/Prometheus viewers and keep original static experiment metrics distinct from a live load test."""
    os.umask(0o077);root=args.runtime_root.resolve();out=args.output.resolve();study=args.study.resolve()
    assert os.geteuid()==0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root/'outputs') and study.is_relative_to(root/'outputs')
    seal=json.loads((ROOT.parent/'manifest.json').read_bytes());assert seal['format']=='iot-llm-ab-view-source-v1'
    for name,pin in seal['files'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==pin
    accepted=json.loads((study/'verification.json').read_bytes());assert accepted['passed'] and accepted['measured_requests']==24 and accepted['warmup_requests']==8
    assert re.fullmatch('llm-model-study-[a-z0-9-]+',study.name)
    out.mkdir(exist_ok=False);opener=build_opener(ProxyHandler({}));asset=json.loads((ROOT/'infra/llm-ab/dashboard.json').read_bytes())
    asset['templating']['list'][0]['current']={'text':study.name,'value':study.name}
    def save(name,value):
        """Retain finite public dashboard and query receipts."""
        (out/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    def fetch(name,url):
        """Preserve each original private viewer response before checking its result."""
        with opener.open(url,timeout=30) as response:raw=response.read(4000000);assert response.status==200 and len(raw)<4000000
        (out/name).write_bytes(raw);return json.loads(raw)
    before=fetch('dashboards-before.json','http://127.0.0.1:13001/api/search?type=dash-db');assert not any(v['uid']==asset['uid'] for v in before)
    pid=subprocess.check_output(['systemctl','show','iot-grafana-28','-p','MainPID','--value']).strip();assert int(pid)>0
    destination=root/'data/runtime/monitoring-28/dashboards/llm-ab.json';assert not destination.exists()
    save('source.json',seal);save('comparison-acceptance.json',accepted);save('dashboard-planned.json',asset)
    destination.write_bytes((out/'dashboard-planned.json').read_bytes());destination.chmod(0o644)
    for attempt in range(15):
        listing=fetch('dashboards-after-'+str(attempt)+'.json','http://127.0.0.1:13001/api/search?type=dash-db')
        if any(v['uid']==asset['uid'] for v in listing):break
        time.sleep(2)
    else:raise ValueError('The new comparison dashboard has not been provisioned')
    assert {v['uid'] for v in before}<={v['uid'] for v in listing}
    actual=fetch('dashboard-native.json','http://127.0.0.1:13001/api/dashboards/uid/'+asset['uid'])['dashboard'];assert actual['panels']==asset['panels']
    stamp=time.time();panels=[]
    for panel in asset['panels']:
        for target in panel.get('targets',[]):
            expression=target['expr'].replace('$run',study.name);suffix='/api/v1/query?'+urlencode({'query':expression,'time':str(stamp)})
            label='panel-'+str(panel['id']);direct=fetch(label+'-prometheus.json','http://127.0.0.1:19091'+suffix)
            proxy=fetch(label+'-grafana.json','http://127.0.0.1:13001/api/datasources/proxy/uid/iot-host-prometheus'+suffix)
            assert direct['status']==proxy['status']=='success' and direct['data']==proxy['data'] and len(direct['data']['result'])==2
            panels.append({'id':panel['id'],'expression':expression,'values':{v['metric']['variant']:v['value'][1] for v in direct['data']['result']}})
    assert len(panels)==8
    assert {k:float(v) for k,v in panels[0]['values'].items()}=={'small':12,'large':12}
    for variant,row in accepted['summary'].items():assert float(panels[1]['values'][variant])==row['correct_contract_answers']/12
    assert subprocess.check_output(['systemctl','show','iot-grafana-28','-p','MainPID','--value']).strip()==pid
    save('panels.json',panels);report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':seal['commit'],
        'study_run':study.name,'dashboard_uid':asset['uid'],'numeric_panel_matches':8,'original_dashboards_preserved':True,'grafana_main_pid':int(pid),
        'scope':'Original completed model-artifact experiment displayed through the retained private Grafana datasource, with exact numerical Prometheus parity. Browser/gateway captures and agent-configuration A/B remain separate.'}
    save('verification.json',report);print(json.dumps(report))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output','study']:p.add_argument('--'+name,type=Path,required=True)
    run(p.parse_args())
