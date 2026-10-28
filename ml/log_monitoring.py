"""Render bounded private Loki/Alloy collection for observed coursework host stdout and application CRI logs."""
from pathlib import Path,PurePosixPath
import json,re

DATASOURCE_UID='iot-logs-loki'
HOST_SERVICES=('node-exporter','prometheus','grafana')
QUERIES=[(1,'Lines by service','sum by (service) (count_over_time({host="$host",collection="$collection"}[1m]))'),
    (2,'Application output streams','sum by (service,stream) (count_over_time({host="$host",collection="$collection",source="cri"}[1m]))'),
    (3,'Host service logs','{host="$host",collection="$collection",source="host-file"}'),
    (4,'Application logs','{host="$host",collection="$collection",source="cri"}')]


def host_alias(value):
    """Keep a bounded anonymous host label outside the query language."""
    if not isinstance(value,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}',value):raise ValueError('Invalid host alias')
    return value


def absolute(value):
    """Accept an explicit Linux path without traversal, newlines or glob patterns."""
    if not isinstance(value,str) or not value.startswith('/') or re.search(r'[\x00-\x1f*?\[\]{}]',value):raise ValueError('Use an explicit Linux path')
    result=PurePosixPath(value)
    if '..' in result.parts:raise ValueError('Path traversal is not a log source')
    return result


def targets(project_root,observed,host,collection='native-28-keep'):
    """Derive each source path from its observed service or Pod identity; never accept arbitrary file reads."""
    root=absolute(project_root);host_alias(host);host_alias(collection)
    sources=observed['sources'];pods=observed['pods']
    if len(sources)!=3 or {s['service'] for s in sources}!=set(HOST_SERVICES):raise ValueError('Require the three observed host services')
    files=[]
    for source in sources:
        service=source['service'];path=absolute(source['path'])
        if path!=root/'outputs/linux-monitoring-28'/(service+'.txt') or source['time_key'] not in ['time','ts','t']:
            raise ValueError('Host log path or timestamp key differs from the observed profile')
        files.append({'path':str(path),'service':service,'time_key':source['time_key'],
            'labels':{'host':host,'collection':collection,'service':service,'source':'host-file'}})
    if not 2<=len(pods)<=8 or not {'predictor','feature-api'}<={p['service'] for p in pods}:raise ValueError('Require the two application services with a bounded Pod list')
    paths=set();cri=[]
    for pod in pods:
        if (pod['namespace'] not in ['iot-serving','iot-features'] or pod['service'] not in ['predictor','feature-api','ab-api','drift-api']
                or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,251}',pod['pod'])
                or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}',pod['container'])
                or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',pod['uid'])
                or type(pod['rotation']) is not int or not 0<=pod['rotation']<=999):raise ValueError('Invalid observed Pod log identity')
        path=PurePosixPath('/var/log/pods')/(pod['namespace']+'_'+pod['pod']+'_'+pod['uid'])/pod['container']/(str(pod['rotation'])+'.log')
        if pod['path']!=str(path) or str(path) in paths:raise ValueError('Pod path differs from its unique identity')
        paths.add(str(path));cri.append({'path':str(path),'labels':{'host':host,'collection':collection,'source':'cri',
            **{n:pod[n] for n in ['service','namespace','pod','container']}}})
    return {'host_files':files,'cri_files':cri}


def dashboard(host,collection='native-28-keep'):
    """Keep four real LogQL panels separate from model accuracy, metrics and distributed traces."""
    host_alias(host);host_alias(collection);panels=[]
    for identifier,title,expression in QUERIES:
        logs=identifier>2
        panels.append({'id':identifier,'title':title,'type':'logs' if logs else 'timeseries',
            'datasource':{'type':'loki','uid':DATASOURCE_UID},'targets':[{'refId':'A','expr':expression,'queryType':'range','editorMode':'code','legendFormat':'{{service}} {{stream}}'}],
            'gridPos':{'x':((identifier-1)%2)*12,'y':0 if not logs else 8,'w':12,'h':16 if logs else 8},
            'options':{'showLabels':True,'showTime':True,'wrapLogMessage':True,'sortOrder':'Descending','enableLogDetails':True} if logs else {'legend':{'displayMode':'list','placement':'bottom'}},
            'fieldConfig':{'defaults':{'unit':'short','min':0},'overrides':[]}})
    return {'uid':'iot-linux-logs','title':'IoT logs / Linux host and APIs','schemaVersion':41,'version':1,'editable':False,
        'description':'Observed host stdout files and explicitly named Kubernetes CRI files. Original log timestamps when parseable; otherwise arrival time. No model-quality or trace claims.',
        'timezone':'utc','refresh':'5s','time':{'from':'now-3h','to':'now'},'tags':['iot','logs','linux'],
        'templating':{'list':[{'name':name,'type':'constant','query':value,'current':{'text':value,'value':value},'hide':0} for name,value in [('host',host),('collection',collection)]]},'panels':panels}


def render(runtime_root,project_root,observed,host='vm-linux',collection='native-28-keep'):
    """Use single-node filesystem storage and fixed loopback endpoints with explicit small caches and retention."""
    root=absolute(runtime_root);selected=targets(project_root,observed,host,collection)
    q=lambda value:json.dumps(value,ensure_ascii=True)
    mapping=lambda values:'{'+', '.join(k+' = '+q(v) for k,v in values.items())+'}'
    config=['logging { level = "info" }','loki.write "local" {\n  endpoint { url = "http://127.0.0.1:13100/loki/api/v1/push" }\n}']
    for index,source in enumerate(selected['host_files']):
        name='host_'+str(index);target=mapping({'__path__':source['path'],**source['labels']})
        config.append(f'''loki.source.file "{name}" {{
  targets = [{target}]
  forward_to = [loki.process.{name}.receiver]
}}
loki.process "{name}" {{
  forward_to = [loki.relabel.paths.receiver]
  stage.logfmt {{ mapping = {{ event_time = {q(source['time_key'])}, level = "level" }} }}
  stage.timestamp {{
    source = "event_time"
    format = "RFC3339Nano"
    action_on_failure = "skip"
    action_on_duplicate_timestamp = "keep"
  }}
  stage.labels {{ values = {{ level = "" }} }}
}}''')
    cri=',\n    '.join(mapping({'__path__':s['path'],**s['labels']}) for s in selected['cri_files'])
    config.append(f'''loki.source.file "applications" {{
  targets = [{cri}]
  forward_to = [loki.process.applications.receiver]
}}
loki.process "applications" {{
  forward_to = [loki.relabel.paths.receiver]
  stage.cri {{}}
}}
loki.relabel "paths" {{
  forward_to = [loki.write.local.receiver]
  rule {{
    action = "labeldrop"
    regex = "filename"
  }}
}}''')
    loki={'auth_enabled':False,'server':{'http_listen_address':'127.0.0.1','http_listen_port':13100,
        'grpc_listen_address':'127.0.0.1','grpc_listen_port':19096,'log_level':'info'},
        'common':{'instance_addr':'127.0.0.1','path_prefix':str(root/'loki'),
            'storage':{'filesystem':{'chunks_directory':str(root/'loki/chunks'),'rules_directory':str(root/'loki/rules')}},
            'replication_factor':1,'ring':{'kvstore':{'store':'inmemory'}}},
        'schema_config':{'configs':[{'from':'2020-10-24','store':'tsdb','object_store':'filesystem','schema':'v13','index':{'prefix':'index_','period':'24h'}}]},
        'query_range':{'results_cache':{'cache':{'embedded_cache':{'enabled':True,'max_size_mb':32}}}},
        'chunk_store_config':{'chunk_cache_config':{'embedded_cache':{'enabled':True,'max_size_mb':32}}},
        'limits_config':{'retention_period':'24h','ingestion_rate_mb':1,'ingestion_burst_size_mb':2,'max_query_parallelism':2,'query_timeout':'30s'},
        'compactor':{'working_directory':str(root/'loki/compactor'),'retention_enabled':True,'delete_request_store':'filesystem'},
        'analytics':{'reporting_enabled':False}}
    datasource={'apiVersion':1,'datasources':[{'uid':DATASOURCE_UID,'name':'IoT private Loki','type':'loki','access':'proxy',
        'url':'http://127.0.0.1:13100','isDefault':False,'editable':False,'jsonData':{'maxLines':1000,'timeout':30}}]}
    return {'loki':loki,'alloy':'\n\n'.join(config)+'\n','datasource':datasource,'dashboard':dashboard(host,collection),'sources':selected}


def write_bundle(output,project_root,observed,host='vm-linux',collection='native-28-keep'):
    """Write a new runtime bundle; preserving older positions and log stores is an operator responsibility."""
    root=Path(output).resolve();documents=render(root.as_posix(),project_root,observed,host,collection);root.mkdir(parents=True,exist_ok=False)
    for name,key in [('loki.yaml','loki'),('grafana-datasource.yaml','datasource'),('dashboard.json','dashboard'),('sources.json','sources')]:
        (root/name).write_text(json.dumps(documents[key],indent=2)+'\n',encoding='utf-8')
    (root/'config.alloy').write_text(documents['alloy'],encoding='utf-8')
    return documents
