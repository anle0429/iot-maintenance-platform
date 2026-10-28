"""Provision private CPU, memory, filesystem and network dashboards with explicit Windows/Linux metric profiles."""
from copy import deepcopy
from pathlib import Path
import json,re
from ml.ab_monitoring import private_target

DATASOURCE_UID='iot-host-prometheus'


def profile(family):
    """Keep exporter-specific names and collector sets explicit; never guess an OS from missing metrics."""
    if family not in ['windows','linux']:raise ValueError('Choose windows or linux explicitly')
    return json.loads((Path(__file__).resolve().parents[1]/'infra/monitoring/host-profiles.json').read_text(encoding='utf-8'))['profiles'][family]


def queries(family,host):
    """Bind only a validated alias to the eleven actual dashboard target expressions."""
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}',host):raise ValueError('Use a short anonymous host alias')
    return [{'panel':p['id'],'refId':chr(65+i),'expr':t['expr'].replace('$host',host),'legendFormat':t['legendFormat']}
        for p in profile(family)['panels'] for i,t in enumerate(p['targets'])]


def exporter_arguments(family,port):
    """Read only bounded collectors and bind the standalone exporter to loopback; install no service."""
    if type(port) is not int or not 1024<=port<=65535:raise ValueError('Choose an unprivileged exporter port')
    item=profile(family)
    if family=='windows':return [f'--web.listen-address=127.0.0.1:{port}','--collectors.enabled='+','.join(item['collectors']),'--collector.logical_disk.volume-include=^[A-Z]:$']
    return [f'--web.listen-address=127.0.0.1:{port}','--collector.disable-defaults',*[f'--collector.{n}' for n in item['collectors']],
        '--collector.filesystem.mount-points-include=^(/|/srv/iot-data|/srv/iot-fast)$']


def dashboard(family,host,label):
    """Expose units and environment in nine panels; missing series remain missing rather than becoming fake zeroes."""
    queries(family,host)
    if not re.fullmatch(r'[A-Za-z0-9 ,()/.-]{1,100}',label):raise ValueError('Use a short printable scope label')
    panels=[]
    for index,spec in enumerate(profile(family)['panels']):
        targets=[{**t,'refId':chr(65+i)} for i,t in enumerate(deepcopy(spec['targets']))]
        unit=spec['unit'];stat=spec['id'] in [3,5,8,9]
        defaults={'unit':unit,'min':0,'color':{'mode':'palette-classic'}}
        if unit=='percent':defaults['max']=100
        if spec['id'] in [8,9]:defaults.update(max=1,color={'mode':'thresholds'},thresholds={'mode':'absolute','steps':[{'color':'red','value':None},{'color':'green','value':1}]})
        panels.append({'id':spec['id'],'title':spec['title'],'type':'stat' if stat else 'timeseries','datasource':{'type':'prometheus','uid':DATASOURCE_UID},
            'targets':targets,'gridPos':{'x':(index%3)*8,'y':(index//3)*7,'w':8,'h':7},'fieldConfig':{'defaults':defaults,'overrides':[]},
            'options':{'reduceOptions':{'calcs':['lastNotNull'],'values':False}} if stat else {'legend':{'displayMode':'list','placement':'bottom'}}})
    return {'id':None,'uid':'iot-host-'+family,'title':'IoT host / '+label,'schemaVersion':39,'version':1,'editable':False,'timezone':'utc','refresh':'5s',
        'description':'Actual host-wide metrics, including other applications.30s rates require15samples at2s intervals; they use boundary extrapolation. Virtual NICs can double-count traffic. No per-job attribution, benchmark, VM deployment or model-quality claim.',
        'time':{'from':'now-5m','to':'now'},'tags':['iot','host',family],
        'templating':{'list':[{'name':'host','label':'Host alias','type':'query','datasource':{'type':'prometheus','uid':DATASOURCE_UID},
            'query':'label_values(up{job="iot-host"}, host)','refresh':1,'multi':False,'includeAll':False,'current':{'text':host,'value':host}}]},'panels':panels}


def write_bundle(destination,endpoint,family,host,label,prometheus_port=19091,grafana_port=13001):
    """Create new isolated state and a metric allowlist that drops address/MAC/process information before ingestion."""
    target=private_target(endpoint);rendered=dashboard(family,host,label);item=profile(family)
    if any(type(p) is not int or not 1024<=p<=65535 for p in [prometheus_port,grafana_port]) or prometheus_port==grafana_port:raise ValueError('Choose distinct unprivileged monitor ports')
    root=Path(destination).resolve()
    if any(c in str(root) for c in '\r\n\x00'):raise ValueError('Invalid runtime path')
    root.mkdir(parents=True,exist_ok=False)
    for name in ['targets','dashboards','provisioning/datasources','provisioning/dashboards','provisioning/plugins','provisioning/alerting','grafana-data','grafana-logs','prometheus-data']:(root/name).mkdir(parents=True)
    def dump(name,value):'Persist a host dashboard asset as strict UTF-8 JSON.';(root/name).write_bytes(json.dumps(value,indent=2,allow_nan=False).encode()+b'\n')
    dump('targets/host.json',[{'targets':[target],'labels':{'host':host}}])
    dump('prometheus.json',{'global':{'scrape_interval':'2s','scrape_timeout':'1s','evaluation_interval':'2s'},'scrape_configs':[{'job_name':'iot-host','sample_limit':5000,
        'file_sd_configs':[{'files':[(root/'targets/host.json').as_posix()],'refresh_interval':'2s'}],
        'metric_relabel_configs':[{'source_labels':['__name__'],'regex':'|'.join(item['metric_names']),'action':'keep'}]}]})
    dump('provisioning/datasources/prometheus.yaml',{'apiVersion':1,'datasources':[{'name':'IoT host Prometheus','uid':DATASOURCE_UID,'type':'prometheus','access':'proxy',
        'url':f'http://127.0.0.1:{prometheus_port}','isDefault':True,'editable':False,'jsonData':{'httpMethod':'POST','timeInterval':'2s'}}]})
    dump('provisioning/dashboards/host.yaml',{'apiVersion':1,'providers':[{'name':'iot-host','folder':'IoT host','type':'file','disableDeletion':True,'allowUiUpdates':False,
        'updateIntervalSeconds':10,'options':{'path':(root/'dashboards').as_posix()}}]})
    dump('dashboards/host.json',rendered)
    (root/'grafana.ini').write_text(f'''[server]
http_addr = 127.0.0.1
http_port = {grafana_port}
[paths]
data = {root.as_posix()}/grafana-data
logs = {root.as_posix()}/grafana-logs
plugins = {root.as_posix()}/plugins
provisioning = {root.as_posix()}/provisioning
[analytics]
reporting_enabled = false
check_for_updates = false
check_for_plugin_updates = false
[users]
allow_sign_up = false
[auth.anonymous]
enabled = true
org_role = Viewer
[plugins]
preinstall_disabled = true
[log]
mode = console
level = warn
''',encoding='utf-8')
    launch={'prometheus_arguments':['--config.file='+str(root/'prometheus.json'),'--storage.tsdb.path='+str(root/'prometheus-data'),
        '--storage.tsdb.retention.time=24h','--storage.tsdb.retention.size=256MB',f'--web.listen-address=127.0.0.1:{prometheus_port}'],
        'grafana_arguments':['server','--config='+str(root/'grafana.ini')],'prometheus_url':f'http://127.0.0.1:{prometheus_port}',
        'grafana_url':f'http://127.0.0.1:{grafana_port}','family':family,'host_alias':host,'datasource_uid':DATASOURCE_UID,'dashboard_uid':rendered['uid']}
    dump('launch.json',launch);return launch
