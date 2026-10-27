"""Plan isolated A/B deployment and verify real HTTP trials before attaching native Kubernetes identities."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import PurePosixPath
import ipaddress,json,math,re,time
from urllib.parse import urlsplit
from ml.serving_ci import check_prediction
from services.predictor.binding import validate_pin

VARIANTS=('baseline','candidate')


def canonical(value):
    """Hash semantic JSON without accepting nonfinite values or whitespace-dependent identities."""
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def baseline_target(deployment):
    """Discover the old predictor from the actual active API rather than hardcoding a model version."""
    api=next(c for c in deployment['spec']['template']['spec']['containers'] if c['name']=='api')
    entries=[e for e in api['env'] if e['name']=='IOT_KSERVE_URL']
    if len(entries)!=1:raise ValueError('Find exactly one active predictor URL')
    value=urlsplit(entries[0]['value'])
    match=re.fullmatch(r'([a-z0-9-]+)-predictor\.iot-serving\.svc\.cluster\.local',value.hostname or '')
    if not match or value.scheme!='http' or value.port not in [None,80] or value.username or value.password or value.path not in ['','/'] or value.query or value.fragment:
        raise ValueError('Active predictor must be the fixed private KServe service')
    return match[1],match[1]+'-predictor'


def private_endpoint(value):
    """Accept only an observed IPv4 ClusterIP with no caller-controlled path, port or credentials."""
    url=urlsplit(value);address=ipaddress.ip_address(url.hostname or '')
    if (url.scheme!='http' or url.port is not None or url.username or url.password or url.path or url.query or url.fragment
            or address.version!=4 or not address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast):
        raise ValueError('A/B configuration requires an observed private IPv4 service')
    return value


def deployment_plan(pair,baseline,baseline_url,candidate_url,reference_raw,template,ledger_path,node_hostname):
    """Freeze config/reference in new immutable ConfigMaps and retain the experiment ledger on its own PVC."""
    baseline=validate_pin(baseline);candidate=validate_pin(pair['pin']);release=pair['release']
    if (not re.fullmatch(r'serve-jenkins-[1-9][0-9]{0,9}',release) or baseline['version']==candidate['version']
            or candidate['production'] is not True or len(reference_raw.encode())>700_000):raise ValueError('Invalid staged model pair or oversized reference')
    reference=json.loads(reference_raw)
    if reference['lineage']['protocol_sha256']!=candidate['protocol_sha256']:raise ValueError('Reference uses another feature protocol')
    config={'experiment':release,'baseline':{'pin':baseline,'url':private_endpoint(baseline_url)},
        'candidate':{'pin':candidate,'url':private_endpoint(candidate_url)}}
    if baseline_url==candidate_url:raise ValueError('Two model versions require distinct endpoints')
    path=PurePosixPath(ledger_path)
    if (not path.is_relative_to('/srv/iot-data') or '..' in path.parts or path.parts[-4:]!=('releases',release,'runtime','ab-ledger')
            or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,62}',node_hostname)):raise ValueError('Bind the ledger to this release on the observed HDD node')
    name='iot-ab-'+release;labels={'iot.coursework/release':release};objects=[]
    for suffix,data in [('config',{'config.json':canonical(config).decode()}),('reference',{'reference.json':reference_raw})]:
        objects.append({'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':name+'-'+suffix,'namespace':'iot-features','labels':labels},'immutable':True,'data':data})
    objects.append({'apiVersion':'v1','kind':'PersistentVolume','metadata':{'name':name+'-ledger','labels':labels},
        'spec':{'capacity':{'storage':'1Gi'},'volumeMode':'Filesystem','accessModes':['ReadWriteOnce'],'persistentVolumeReclaimPolicy':'Retain',
            'storageClassName':'','local':{'path':str(path)},'nodeAffinity':{'required':{'nodeSelectorTerms':[{'matchExpressions':[
                {'key':'kubernetes.io/hostname','operator':'In','values':[node_hostname]}]}]}}}})
    objects.append({'apiVersion':'v1','kind':'PersistentVolumeClaim','metadata':{'name':name+'-ledger','namespace':'iot-features','labels':labels},
        'spec':{'accessModes':['ReadWriteOnce'],'storageClassName':'','volumeName':name+'-ledger','resources':{'requests':{'storage':'1Gi'}}}})
    values=deepcopy(template);tag='iot-coursework-ab-api:'+release
    values['image']={'repository':'iot-coursework-ab-api','tag':release,'pullPolicy':'Never'}
    values['revisionMarker']=release;values['replicas']=1;values['autoscaling']={'enabled':False}
    values['initContainers'][0]['image']=tag
    for entry in values['env']:
        if entry['name']=='IOT_FEAST_REDIS':entry['value']=next(e['value'] for e in pair['feature_values']['env'] if e['name']==entry['name'])
    for volume in values['volumes']:
        if volume['name']=='config':volume['configMap']['name']=name+'-config'
        elif volume['name']=='reference':volume['configMap']['name']=name+'-reference'
        elif volume['name']=='seed':volume['configMap']['name']=pair['seed_configmap']
        elif volume['name']=='ledger':volume['persistentVolumeClaim']['claimName']=name+'-ledger'
    return {'commit':pair['commit'],'release':release,'helm_release':name,'image':tag,'objects':objects,'values':values,
        'config':config,'reference_sha256':sha256(reference_raw.encode()).hexdigest()}


def trial_jobs(context,experiment,cycles=4):
    """Declare a bounded workload with at least32 operating machines per stable cohort before sending traffic."""
    if type(cycles) is not int or not 1<=cycles<=4:raise ValueError('Bound experiment repetitions to1..4')
    machines=sorted(m for m,status in context['machines'].items() if status['status']=='operating')
    groups={m:VARIANTS[sha256((experiment+'\0'+m).encode()).digest()[0]//128] for m in machines}
    if any(sum(v==variant for v in groups.values())<32 for variant in VARIANTS) or len(machines)*cycles>2048:
        raise ValueError('Need32unique operating snapshots per cohort within the retained trial capacity')
    jobs=[{'machine_id':m,'variant':groups[m],'request_id':f'{experiment}:{cycle}:{m}'} for cycle in range(cycles) for m in machines]
    if any(sum(j['variant']==v for j in jobs)<100 for v in VARIANTS):raise ValueError('Need100requests per cohort; repetitions are not independent statistical samples')
    return jobs


def metric_value(raw,name,labels,missing_zero=False):
    """Read one exact bounded metric series; reject duplicates or negative/nonfinite counter values."""
    from prometheus_client.parser import text_string_to_metric_families
    values=[s.value for f in text_string_to_metric_families(raw) for s in f.samples if s.name==name and s.labels==labels]
    if not values and missing_zero:return 0.
    if len(values)!=1 or not math.isfinite(values[0]) or values[0]<0:raise ValueError('Missing, duplicate or invalid metric series: '+name)
    return values[0]


def check_trace(row,job,context,offline,pins):
    """Verify each routed response against an independently queried immutable offline snapshot and both model pins."""
    if (any(row.get(n)!=job[n] for n in ['request_id','machine_id','variant']) or row.get('status')!=200
            or type(row.get('latency_ms')) not in [int,float] or not math.isfinite(row['latency_ms']) or row['latency_ms']<0):
        raise ValueError('A/B HTTP outcome/assignment differs from the declared request')
    check_prediction(row['prediction'],row['model'],pins[job['variant']])
    trace=row['feature_trace'];expected=offline[job['machine_id']]
    age=(datetime.fromisoformat(context['as_of'])-datetime.fromisoformat(expected['feature_event_ts'])).total_seconds()
    if (trace['machine_id']!=job['machine_id'] or trace['clock_mode']!='simulation'
            or any(trace[n]!=context[n] for n in ['as_of','prepared_wall_utc','protocol_sha256','source_manifest_sha256'])
            or trace['operating_context']!=context['machines'][job['machine_id']] or not 0<=age<=600 or trace['feature_age_seconds']!=age
            or datetime.fromisoformat(trace['feature_event_ts'])!=datetime.fromisoformat(expected['feature_event_ts'])
            or set(trace['features'])!=set(expected['features'])
            or any(type(value) not in [int,float] or not math.isfinite(value) or not math.isclose(value,expected['features'][n],rel_tol=1e-12,abs_tol=1e-12) for n,value in trace['features'].items())):
        raise ValueError('A/B feature trace differs from the native seed or independent offline lookup')


def probe(call,text,predictor_metrics,context,experiment,pins,reference_id,offline,cycles=4):
    """Verify actual A/B HTTP/metrics and replay while leaving native UID authorization to the Kubernetes controller."""
    jobs=trial_jobs(context,experiment,cycles);code,ready=call('/health/ready')
    if (code!=200 or ready.get('ready') is not True or ready.get('experiment')!=experiment
            or ready.get('reference_id')!=reference_id or ready.get('pins')!=pins):raise ValueError('A/B readiness identity differs')
    code,empty=call('/v1/experiment')
    if (code!=200 or empty.get('experiment')!=experiment or empty.get('reference_id')!=reference_id
            or empty.get('baseline_pin')!=pins['baseline'] or empty.get('candidate_pin')!=pins['candidate']
            or set(empty['metrics'])!=set(VARIANTS) or empty['observations'] or any(m['requests'] or m['failures'] for m in empty['metrics'].values())):
        raise ValueError('Start a new empty experiment; never reuse a prior ledger as a fresh CI run')
    before={v:predictor_metrics(v) for v in VARIANTS};observed={};client=[]
    for job in jobs:
        started=time.perf_counter();code,row=call('/v1/machines/'+job['machine_id']+':predict',{}, {'X-Request-ID':job['request_id']})
        elapsed=1000*(time.perf_counter()-started)
        if code!=200 or row.get('experiment')!=experiment:raise ValueError('A/B trial failed')
        check_trace(row,job,context,offline,pins);observed[job['request_id']]=row
        client.append({'request_id':job['request_id'],'client_elapsed_ms':elapsed,'semantic_response_sha256':sha256(canonical(row)).hexdigest()})
    code,data=call('/v1/experiment')
    if (code!=200 or data['experiment']!=experiment or data['baseline_pin']!=pins['baseline'] or data['candidate_pin']!=pins['candidate']
            or data['ground_truth_used'] is not False or data['reference_id']!=reference_id
            or len(data['observations'])!=len(observed) or {r['request_id']:r for r in data['observations']}!=observed):raise ValueError('Ledger readback differs from actual HTTP outcomes')
    metrics=text('/metrics');after={v:predictor_metrics(v) for v in VARIANTS};deltas={};counts={}
    for variant in VARIANTS:
        count=sum(j['variant']==variant for j in jobs);counts[variant]=count;drift=data['drift'][variant]
        unique=len({(r['machine_id'],r['feature_trace']['feature_event_ts']) for r in observed.values() if r['variant']==variant})
        if (data['metrics'][variant]!={'requests':count,'failures':0} or drift.get('ready') is not True
                or drift['reference_id']!=reference_id or drift['unique_rows']!=unique or unique<32
                or set(drift['features'])!=set(next(iter(offline.values()))['features'])):raise ValueError('Native A/B counters or drift sample identity differs')
        labels={'experiment':experiment,'variant':variant,'version':pins[variant]['version']}
        for name,expected in [('iot_ab_requests_total',count),('iot_ab_failures_total',0),('iot_ab_unique_snapshots',unique),('iot_ab_request_seconds_count',count)]:
            if metric_value(metrics,name,labels)!=expected:raise ValueError('A/B scrape differs from the ledger')
        for name,value in drift['features'].items():
            if metric_value(metrics,'iot_ab_feature_psi',{**labels,'feature':name})!=value['psi']:raise ValueError('A/B PSI scrape differs')
        delta=metric_value(after[variant],'iot_predictor_requests_total',{'outcome':'success'})-metric_value(before[variant],'iot_predictor_requests_total',{'outcome':'success'},True)
        if delta<count:raise ValueError('Upstream predictor counter did not account for routed traffic')
        deltas[variant]=delta
    def replay(job):
        """Use independent HTTP calls across threads; replays must retain both contents and the original trial counts."""
        code,row=call('/v1/machines/'+job['machine_id']+':predict',{}, {'X-Request-ID':job['request_id']})
        if code!=200 or row!=observed[job['request_id']]:raise ValueError('Concurrent A/B retry changed its original outcome')
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(replay,jobs[:40]))
    code,replayed=call('/v1/experiment')
    if code!=200 or replayed!=data or text('/metrics')!=metrics:raise ValueError('Retry changed trial counts or drift/latency metrics')
    return {'readback':data,'metrics':metrics,'predictor_metrics_before':before,'predictor_metrics_after':after,'predictor_counter_deltas':deltas,
        'cohort_counts':counts,'client_observations':client,'replay_checks':40,'offline_lookup_sha256':sha256(canonical(offline)).hexdigest(),
        'scope':'Verified bounded HTTP workload and independent offline/metric readbacks. Upstream deltas may include probes/other traffic. Repeated requests are not independent drift or model-quality samples; no Kubernetes identities fabricated here.'}
