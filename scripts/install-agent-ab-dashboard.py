"""Monitor the completed native agent comparison separately from the model-artifact experiment and reconcile actual Grafana vectors."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import build_opener,ProxyHandler,Request
import argparse,json,os,re,subprocess,time
ROOT=Path(__file__).resolve().parents[1]


def run(args):
    """Keep existing monitoring jobs/dashboards and restore the exact historical model gauges after a Pushgateway restart."""
    root=args.runtime_root.resolve();out=args.output.resolve();study=args.study.resolve()
    assert os.geteuid()==0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root/'outputs') and study.is_relative_to(root/'outputs')
    seal=json.loads((ROOT.parent/'manifest.json').read_bytes());assert seal['format']=='iot-agent-ab-view-source-v1'
    for name,pin in seal['files'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==pin
    accepted=json.loads((study/'verification.json').read_bytes());assert accepted['passed'] and accepted['requests']==6 and re.fullmatch('agent-ab-study-[a-z0-9-]+',study.name)
    os.umask(0o077);out.mkdir(exist_ok=False);opener=build_opener(ProxyHandler({}))
    def save(name,value):
        """Retain finite public numeric observations and original run identities."""
        (out/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    def fetch(name,url):
        """Save each original private read response before checking its status or schema."""
        with opener.open(url,timeout=30) as response:raw=response.read(4000000);assert response.status==200 and len(raw)<4000000
        (out/name).write_bytes(raw);return json.loads(raw)
    config=root/'data/runtime/monitoring-28/prometheus.json';old=config.read_bytes();planned=json.loads(old)
    assert not any(v['job_name']=='iot-agent-ab' for v in planned['scrape_configs'])
    planned['scrape_configs'].append({'job_name':'iot-agent-ab','honor_labels':True,'scrape_interval':'2s','scrape_timeout':'1s','sample_limit':1000,
        'static_configs':[{'targets':['127.0.0.1:19092']}],'metric_relabel_configs':[{'source_labels':['__name__'],'regex':'iot_agent_ab_.*','action':'keep'}]})
    save('source.json',seal);save('comparison-acceptance.json',accepted);(out/'prometheus-before.json').write_bytes(old);save('prometheus-planned.json',planned)
    promtool=root/'data/runtime/monitoring-assets-28/prometheus/prometheus-3.13.5.linux-amd64/promtool'
    result=subprocess.run([str(promtool),'check','config',str(out/'prometheus-planned.json')],capture_output=True,timeout=30)
    (out/'promtool.stdout').write_bytes(result.stdout);(out/'promtool.stderr').write_bytes(result.stderr);assert result.returncode==0
    prom_pid=subprocess.check_output(['systemctl','show','iot-prometheus-28','-p','MainPID','--value']).strip();assert int(prom_pid)>0 and config.read_bytes()==old
    pending=config.with_suffix('.agent-ab.pending');pending.write_bytes((out/'prometheus-planned.json').read_bytes());pending.chmod(0o644);pending.replace(config)
    subprocess.run(['systemctl','kill','--kill-who=main','--signal=HUP','iot-prometheus-28'],check=True,timeout=30)
    # Restore a historical completed result, not fresh model requests or a new completion time.
    inventory=root/'outputs/llm-model-ab-capture-30a/manifest.json';assert sha256(inventory.read_bytes()).hexdigest()=='14a3beb8eea638850328c5fabcb8612acec927acdc5fd21ad4a3be679c41abbd'
    pin=json.loads(inventory.read_bytes())['files']['native/llm-model-study-30c/experiment.prom'];metrics=(root/'outputs/llm-model-study-30c/experiment.prom').read_bytes()
    assert len(metrics)==pin['bytes'] and sha256(metrics).hexdigest()==pin['sha256']
    historical=json.loads((root/'outputs/llm-model-study-30c/verification.json').read_bytes());assert historical['passed'] and historical['source_commit']=='0c8d00c2d1f65d8e14f8ff4ea1b0e169b98fe0ca'
    with opener.open(Request('http://127.0.0.1:19092/metrics/job/iot-llm-ab/run/llm-model-study-30c',data=metrics,method='PUT',headers={'Content-Type':'text/plain; version=0.0.4'}),timeout=15) as response:assert response.status==200
    save('historical-model-metrics-restored.json',{'passed':True,'original_run':'llm-model-study-30c','original_completed_utc':historical['checked_utc'],
        'original_prometheus_payload_sha256':pin['sha256'],'new_model_requests':0,'original_payload_unchanged':True,'scope':'Completed historical gauges restored after CPU/Pushgateway restart. Ingestion time is not execution time.'})
    asset=json.loads((ROOT/'infra/llm-ab/agent-dashboard.json').read_bytes());asset['templating']['list'][0]['current']={'text':study.name,'value':study.name}
    before=fetch('dashboards-before.json','http://127.0.0.1:13001/api/search?type=dash-db');assert not any(v['uid']==asset['uid'] for v in before)
    grafana_pid=subprocess.check_output(['systemctl','show','iot-grafana-28','-p','MainPID','--value']).strip();assert int(grafana_pid)>0
    destination=root/'data/runtime/monitoring-28/dashboards/agent-ab.json';assert not destination.exists();save('dashboard-planned.json',asset)
    destination.write_bytes((out/'dashboard-planned.json').read_bytes());destination.chmod(0o644)
    for attempt in range(30):
        listing=fetch('dashboards-after-'+str(attempt)+'.json','http://127.0.0.1:13001/api/search?type=dash-db')
        if any(v['uid']==asset['uid'] for v in listing):break
        time.sleep(2)
    else:raise ValueError('The new agent dashboard was not provisioned')
    assert {v['uid'] for v in before}<={v['uid'] for v in listing}
    actual=fetch('dashboard-native.json','http://127.0.0.1:13001/api/dashboards/uid/'+asset['uid'])['dashboard'];assert actual['panels']==asset['panels']
    query='iot_agent_ab_requests{run="'+study.name+'"}'
    for attempt in range(20):
        values=fetch('scrape-readback-'+str(attempt)+'.json','http://127.0.0.1:19091/api/v1/query?'+urlencode({'query':query}))['data']['result']
        if len(values)==2 and all(float(v['value'][1])==3 for v in values):break
        time.sleep(2)
    else:raise ValueError('Both actual agent variants were not scraped')
    stamp=time.time();panels=[]
    for panel in asset['panels']:
        for target in panel.get('targets',[]):
            expression=target['expr'].replace('$run',study.name);suffix='/api/v1/query?'+urlencode({'query':expression,'time':str(stamp)})
            label='panel-'+str(panel['id']);direct=fetch(label+'-prometheus.json','http://127.0.0.1:19091'+suffix)
            proxy=fetch(label+'-grafana.json','http://127.0.0.1:13001/api/datasources/proxy/uid/iot-host-prometheus'+suffix)
            assert direct['status']==proxy['status']=='success' and direct['data']==proxy['data'] and len(direct['data']['result'])==2
            panels.append({'id':panel['id'],'expression':expression,'values':{v['metric']['variant']:v['value'][1] for v in direct['data']['result']}})
    assert len(panels)==3 and {k:float(v) for k,v in panels[0]['values'].items()}=={'a':3,'b':3}
    for variant,row in accepted['summary'].items():
        assert float(panels[1]['values'][variant])==row['tool_grounded_answers']/3
        assert float(panels[2]['values'][variant])==row['median_complete_seconds']
    assert subprocess.check_output(['systemctl','show','iot-prometheus-28','-p','MainPID','--value']).strip()==prom_pid
    assert subprocess.check_output(['systemctl','show','iot-grafana-28','-p','MainPID','--value']).strip()==grafana_pid
    save('panels.json',panels);save('verification.json',{'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':seal['commit'],
        'study_run':study.name,'dashboard_uid':asset['uid'],'numeric_panel_matches':3,'original_jobs_and_dashboards_preserved':True,
        'scope':'Completed native agent outcomes displayed separately from the historical model-artifact study. No general agent-quality ranking or automatic promotion.'})
    print(json.dumps({'passed':True,'numeric_panel_matches':3,'dashboard_uid':asset['uid']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output','study']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
