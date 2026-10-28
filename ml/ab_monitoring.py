"""Render private Prometheus/Grafana configuration for the retained two-version A/B ledger."""
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit
import ipaddress,json,re

DATASOURCE_UID='iot-ab-prometheus'
DASHBOARD_UID='iot-two-model-ab'


def private_target(endpoint):
    """Accept an explicit loopback/RFC1918 IPv4 endpoint; reject public URLs, DNS and URL credentials."""
    parsed=urlsplit(endpoint)
    try:address=ipaddress.ip_address(parsed.hostname or '');port=parsed.port if parsed.port is not None else 80
    except ValueError as error:raise ValueError('Use an explicit private IPv4 HTTP endpoint') from error
    allowed=address==ipaddress.ip_address('127.0.0.1') or any(address in ipaddress.ip_network(n) for n in ['10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'])
    if not allowed or not 1<=port<=65535 or parsed.scheme!='http' or parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('Use an explicit private IPv4 HTTP endpoint without a path')
    return str(address)+':'+str(port)


def dashboard(template,experiment,label):
    """Resolve datasource inputs before file provisioning; dashboard JSON does not expand environment variables."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',experiment):raise ValueError('Invalid experiment label')
    if not re.fullmatch(r'[A-Za-z0-9 ,()/.-]{1,100}',label):raise ValueError('Use a short printable environment label')
    document=deepcopy(template)
    if document.get('uid')!=DASHBOARD_UID or len(document.get('panels',[]))!=6:raise ValueError('Expected the six-panel IoT A/B dashboard')
    def resolve(value):
        'Recursively bind the dashboard datasource placeholder to its reviewed UID.'
        if isinstance(value,str):return value.replace('${DS_PROMETHEUS}',DATASOURCE_UID)
        if isinstance(value,list):return [resolve(x) for x in value]
        if isinstance(value,dict):return {k:resolve(v) for k,v in value.items()}
        return value
    document=resolve(document);document.pop('__inputs',None)
    document.update(id=None,editable=False,title='IoT A/B / '+label)
    document['description']+=' Environment: '+label+'.'
    document['time']={'from':'now-5m','to':'now'}
    for variable in document['templating']['list']:
        if variable['name']=='experiment':variable['current']={'text':experiment,'value':experiment}
    if '${DS_' in json.dumps(document):raise ValueError('Unresolved datasource input')
    return document


def panel_queries(document,experiment):
    """Return the six actual dashboard queries with one validated experiment, for API readback checks."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',experiment):raise ValueError('Invalid experiment label')
    return [{'panel':p['id'],'title':p['title'],'expr':p['targets'][0]['expr'].replace('$experiment',experiment)} for p in document['panels']]


def write_bundle(destination,endpoint,experiment,label,prometheus_port=19090,grafana_port=13000,template=None):
    """Create a fresh runtime folder; never overwrite state, store a password or listen beyond loopback."""
    target=private_target(endpoint)
    if any(type(p) is not int or not 1024<=p<=65535 for p in [prometheus_port,grafana_port]) or prometheus_port==grafana_port:
        raise ValueError('Choose distinct unprivileged Prometheus and Grafana ports')
    if template is None:template=json.loads((Path(__file__).resolve().parents[1]/'infra/ab-api/grafana-dashboard.json').read_text(encoding='utf-8'))
    rendered=dashboard(template,experiment,label)
    root=Path(destination).resolve()
    if any(c in str(root) for c in '\r\n\x00'):raise ValueError('Invalid runtime path')
    root.mkdir(parents=True,exist_ok=False)
    for name in ['targets','dashboards','provisioning/datasources','provisioning/dashboards','provisioning/plugins','provisioning/alerting','grafana-data','grafana-logs','prometheus-data']:(root/name).mkdir(parents=True)
    def dump(name,value):
        'Write one dashboard asset as strict JSON without nonfinite numbers.'
        (root/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    dump('targets/ab.json',[{'targets':[target]}])
    dump('prometheus.json',{'global':{'scrape_interval':'2s','scrape_timeout':'1s','evaluation_interval':'2s'},
        'scrape_configs':[{'job_name':'iot-ab','file_sd_configs':[{'files':[(root/'targets/ab.json').as_posix()],'refresh_interval':'2s'}]}]})
    dump('provisioning/datasources/prometheus.yaml',{'apiVersion':1,'datasources':[{'name':'IoT A/B Prometheus','uid':DATASOURCE_UID,
        'type':'prometheus','access':'proxy','url':f'http://127.0.0.1:{prometheus_port}','isDefault':True,'editable':False,
        'jsonData':{'httpMethod':'POST','timeInterval':'2s'}}]})
    dump('provisioning/dashboards/ab.yaml',{'apiVersion':1,'providers':[{'name':'iot-ab','orgId':1,'folder':'IoT A/B',
        'type':'file','disableDeletion':True,'allowUiUpdates':False,'updateIntervalSeconds':10,'options':{'path':(root/'dashboards').as_posix()}}]})
    dump('dashboards/ab.json',rendered)
    # Admin credentials are supplied via GF_SECURITY_ADMIN_USER/PASSWORD at process launch, never saved here.
    ini=f'''[server]
http_addr = 127.0.0.1
http_port = {grafana_port}
domain = localhost
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
'''
    (root/'grafana.ini').write_text(ini,encoding='utf-8')
    launch={'prometheus_arguments':['--config.file='+str(root/'prometheus.json'),'--storage.tsdb.path='+str(root/'prometheus-data'),
        '--storage.tsdb.retention.time=24h','--storage.tsdb.retention.size=256MB','--web.listen-address=127.0.0.1:'+str(prometheus_port)],
        'grafana_arguments':['server','--config='+str(root/'grafana.ini')],
        'prometheus_url':f'http://127.0.0.1:{prometheus_port}','grafana_url':f'http://127.0.0.1:{grafana_port}',
        'datasource_uid':DATASOURCE_UID,'dashboard_uid':DASHBOARD_UID,'experiment':experiment,
        'security_scope':'Loopback-only diagnostic viewer; credentials via process environment; not final Vault or service authentication.'}
    dump('launch.json',launch)
    return launch
