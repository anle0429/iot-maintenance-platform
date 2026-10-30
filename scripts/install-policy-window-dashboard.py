"""Publish a completed policy-window result without replacing existing dashboards, jobs or observer application code."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,build_opener,ProxyHandler
import argparse,json,os,re,subprocess,time
ROOT=Path(__file__).resolve().parents[1]


def run(args):
    """Install one bounded batch-result job and dashboard, checking every numeric target against actual Prometheus and Grafana."""
    root=args.runtime_root.resolve();out=args.output.resolve();study=args.study.resolve()
    assert os.geteuid()==0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root/'outputs') and study.is_relative_to(root/'outputs')
    seal=json.loads((ROOT.parent/'manifest.json').read_bytes());assert seal['format']=='iot-policy-window-view-source-v1'
    for name,pin in seal['files'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==pin
    accepted=json.loads((study/'verification.json').read_bytes());summary=accepted['summary']
    assert accepted['passed'] and summary['admissions']==12 and summary['blocked_prompts']==9 and summary['total_category_hits']==12
    assert re.fullmatch('policy-window-[a-z0-9-]+',study.name)
    os.umask(0o077);out.mkdir(exist_ok=False);opener=build_opener(ProxyHandler({}))
    def save(name,value):
        """Preserve finite original batch and monitoring observations."""
        (out/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    def fetch(name,url):
        """Keep original private readbacks for direct and datasource-proxied comparisons."""
        with opener.open(url,timeout=30) as response:raw=response.read(4000000);assert response.status==200 and len(raw)<4000000
        (out/name).write_bytes(raw);return json.loads(raw)
    save('source.json',seal);save('study-acceptance.json',accepted)
    config=root/'data/runtime/monitoring-28/prometheus.json';original=config.read_bytes();planned=json.loads(original)
    assert not any(v['job_name']=='iot-llm-policy-window' for v in planned['scrape_configs'])
    planned['scrape_configs'].append({'job_name':'iot-llm-policy-window','honor_labels':True,'scrape_interval':'2s','scrape_timeout':'1s','sample_limit':1000,
        'static_configs':[{'targets':['127.0.0.1:19092']}],'metric_relabel_configs':[{'source_labels':['__name__'],'regex':'iot_llm_policy_window_.*','action':'keep'}]})
    (out/'prometheus-before.json').write_bytes(original);save('prometheus-planned.json',planned)
    tool=root/'data/runtime/monitoring-assets-28/prometheus/prometheus-3.13.5.linux-amd64/promtool'
    result=subprocess.run([str(tool),'check','config',str(out/'prometheus-planned.json')],capture_output=True,timeout=30)
    (out/'promtool.stdout').write_bytes(result.stdout);(out/'promtool.stderr').write_bytes(result.stderr);assert result.returncode==0
    prom_pid=subprocess.check_output(['systemctl','show','iot-prometheus-28','-p','MainPID','--value']).strip();assert int(prom_pid)>0 and config.read_bytes()==original
    temporary=config.with_suffix('.policy-window.pending');temporary.write_bytes((out/'prometheus-planned.json').read_bytes());temporary.chmod(0o644);temporary.replace(config)
    subprocess.run(['systemctl','kill','--kill-who=main','--signal=HUP','iot-prometheus-28'],check=True,timeout=30)
    numbers={'admissions':12,'blocked_prompts':9,'category_hits_total':12,'seconds':summary['observed_seconds'],'blocked_per_minute':summary['blocked_prompts_per_observed_minute']}
    lines=[]
    for name,value in numbers.items():lines+=['# TYPE iot_llm_policy_window_'+name+' gauge','iot_llm_policy_window_'+name+' '+format(value,'.17g')]
    for metric,values in [('category_hits',summary['category_hits']),('category_hits_per_minute',summary['category_hits_per_observed_minute'])]:
        lines.append('# TYPE iot_llm_policy_window_'+metric+' gauge')
        for category,value in values.items():lines.append('iot_llm_policy_window_'+metric+'{category="'+category+'"} '+format(value,'.17g'))
    metrics=('\n'.join(lines)+'\n').encode();(out/'window.prom').write_bytes(metrics)
    with opener.open(Request('http://127.0.0.1:19092/metrics/job/iot-llm-policy-window/run/'+study.name,data=metrics,method='PUT',headers={'Content-Type':'text/plain; version=0.0.4'}),timeout=15) as response:assert response.status==200
    save('batch-gauge-publication.json',{'original_study_run':study.name,'original_completed_utc':accepted['checked_utc'],'published_utc':datetime.now(timezone.utc).isoformat(),
        'new_model_requests':0,'metric_type':'gauge','scope':'A completed bounded batch result, not a live policy counter or another model run.'})
    asset=json.loads((ROOT/'infra/llm-ab/policy-dashboard.json').read_bytes());asset['templating']['list'][0]['current']={'text':study.name,'value':study.name}
    start=datetime.fromisoformat(accepted['started_utc']).timestamp()-10;end=accepted['prometheus_evaluation_unix']
    asset['time']={'from':datetime.fromtimestamp(start,timezone.utc).isoformat(),'to':datetime.fromtimestamp(end,timezone.utc).isoformat()}
    before=fetch('dashboards-before.json','http://127.0.0.1:13001/api/search?type=dash-db');assert not any(v['uid']==asset['uid'] for v in before)
    grafana_pid=subprocess.check_output(['systemctl','show','iot-grafana-28','-p','MainPID','--value']).strip();assert int(grafana_pid)>0
    destination=root/'data/runtime/monitoring-28/dashboards/policy-window.json';assert not destination.exists();save('dashboard-planned.json',asset)
    destination.write_bytes((out/'dashboard-planned.json').read_bytes());destination.chmod(0o644)
    for attempt in range(30):
        listing=fetch('dashboards-after-'+str(attempt)+'.json','http://127.0.0.1:13001/api/search?type=dash-db')
        if any(v['uid']==asset['uid'] for v in listing):break
        time.sleep(2)
    else:raise ValueError('The policy-window dashboard was not provisioned')
    assert {v['uid'] for v in before}<={v['uid'] for v in listing}
    actual=fetch('dashboard-native.json','http://127.0.0.1:13001/api/dashboards/uid/'+asset['uid'])['dashboard'];assert actual['panels']==asset['panels']
    query='iot_llm_policy_window_admissions{run="'+study.name+'"}'
    for attempt in range(20):
        values=fetch('scrape-readback-'+str(attempt)+'.json','http://127.0.0.1:19091/api/v1/query?'+urlencode({'query':query}))['data']['result']
        if len(values)==1 and float(values[0]['value'][1])==12:break
        time.sleep(2)
    else:raise ValueError('Actual completed window gauges were not scraped')
    panels=[]
    for panel in asset['panels']:
        for target in panel.get('targets',[]):
            expression=target['expr'].replace('$run',study.name);is_range=panel['type']=='timeseries'
            query_args={'query':expression,'start':start,'end':end,'step':2} if is_range else {'query':expression,'time':time.time()}
            suffix='/api/v1/'+('query_range' if is_range else 'query')+'?'+urlencode(query_args);label='panel-'+str(panel['id'])
            direct=fetch(label+'-prometheus.json','http://127.0.0.1:19091'+suffix)
            proxy=fetch(label+'-grafana.json','http://127.0.0.1:13001/api/datasources/proxy/uid/iot-host-prometheus'+suffix)
            assert direct['status']==proxy['status']=='success' and direct['data']==proxy['data'] and direct['data']['result']
            if panel['id']<=6:assert len(direct['data']['result'])==1 and float(direct['data']['result'][0]['value'][1])==list(numbers.values())[panel['id']-2]
            elif panel['id'] in [7,8]:
                expected=summary['category_hits' if panel['id']==7 else 'category_hits_per_observed_minute']
                assert {v['metric']['category']:float(v['value'][1]) for v in direct['data']['result']}==expected
            else:assert max(float(v[1]) for series in direct['data']['result'] for v in series['values'])>0
            panels.append({'id':panel['id'],'expression':expression,'kind':'range' if is_range else 'instant','series':len(direct['data']['result'])})
    assert len(panels)==8
    assert subprocess.check_output(['systemctl','show','iot-prometheus-28','-p','MainPID','--value']).strip()==prom_pid
    assert subprocess.check_output(['systemctl','show','iot-grafana-28','-p','MainPID','--value']).strip()==grafana_pid
    save('panels.json',panels);save('verification.json',{'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':seal['commit'],
        'study_run':study.name,'dashboard_uid':asset['uid'],'numeric_panel_matches':8,'original_jobs_and_dashboards_preserved':True,
        'scope':'Seven exact completed-batch targets and one original observer counter-rate graph, each reconciled with the real Grafana datasource. No application changes or new model requests.'})
    print(json.dumps({'passed':True,'numeric_panel_matches':8,'dashboard_uid':asset['uid']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output','study']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
