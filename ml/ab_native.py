"""Deploy and recheck one private A/B experiment using actual Kubernetes identities on the existing VM."""
from datetime import datetime,timezone
from hashlib import sha256
from contextlib import closing
import json,os,re,sqlite3,subprocess
from ml import ab_ci
from ml.serving_ci import check_pods,check_seed_configmap,ab_gate
from services.predictor.binding import FIELDS,validate_pin


def save(path,value):
    """Retain small native receipts atomically inside this release only."""
    pending=path.with_suffix('.pending');pending.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');pending.replace(path)


def pod_identity(pods):
    """Require a stable running container incarnation; a restart must not masquerade as the same counter process."""
    result={}
    for pod in pods['items']:
        if pod['metadata'].get('deletionTimestamp'):continue
        result[pod['metadata']['uid']]={s['name']:{k:s[k] for k in ['containerID','imageID','restartCount']}
            for s in pod['status']['containerStatuses']}
    if not result:raise ValueError('No stable experiment pods')
    return result


def model_state(c,name):
    """Read actual ISVC/service/pod identities and model bytes; never infer deployment from a manifest alone."""
    isvc=c.get('get','inferenceservice',name,'-n','iot-serving')
    if not any(x['type']=='Ready' and x['status']=='True' for x in isvc['status']['conditions']):raise ValueError('InferenceService is not Ready')
    service=c.get('get','service',name+'-predictor','-n','iot-serving');url=ab_ci.private_endpoint(c.service_url(name+'-predictor','iot-serving'))
    selector=service['spec']['selector']
    if not selector:raise ValueError('Predictor service has no pod selector')
    pods=c.get('get','pods','-n','iot-serving','-l',','.join(k+'='+v for k,v in sorted(selector.items())))
    live=[p for p in pods['items'] if not p['metadata'].get('deletionTimestamp')]
    if not live:raise ValueError('Predictor service has no live pods')
    image=isvc['spec']['predictor']['containers'][0]['image']
    config=next(s['imageID'] for s in live[0]['status']['containerStatuses'] if s['name']=='kserve-container').split('://')[-1].split('@')[-1]
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',config):raise ValueError('Predictor lacks a concrete CRI config identity')
    check_pods(pods['items'],image,config,'kserve-container');pins=[];nodes=set()
    code="import json,pathlib,hashlib; from services.predictor.runtime import PumpPredictor; p=pathlib.Path('/app/model'); m=PumpPredictor(p); print(json.dumps({'metadata':m.metadata(),'portable_sha256':hashlib.sha256((p/'portable.json').read_bytes()).hexdigest()}))"
    for pod in live:
        if pod['metadata']['labels'].get('serving.kserve.io/inferenceservice')!=name:raise ValueError('Service selected another model workload')
        raw=json.loads(subprocess.check_output(c.k+['exec','-n','iot-serving',pod['metadata']['name'],'-c','kserve-container','--','python','-c',code],text=True,timeout=30))
        pin=validate_pin({n:raw['metadata'][n] for n in FIELDS})
        if raw['portable_sha256']!=pin['portable_sha256']:raise ValueError('Native predictor weights differ from its HTTP identity')
        pins.append(pin);nodes.add(pod['spec']['nodeName'])
    if any(p!=pins[0] for p in pins) or len(nodes)!=1:raise ValueError('Require one observed node and one baseline model identity')
    return {'name':name,'isvc_uid':isvc['metadata']['uid'],'service_uid':service['metadata']['uid'],'url':url,
        'image':image,'config_digest':config,'pin':pins[0],'pods':pod_identity(pods),'node_name':next(iter(nodes))}


def feature_identity(c):
    """Bind the active routing configuration so a concurrent operator change cannot be overwritten by this release."""
    deployment=c.get('get','deployment','iot-feature-api','-n','iot-features');target=ab_ci.baseline_target(deployment)
    return {'uid':deployment['metadata']['uid'],'generation':deployment['metadata']['generation'],'isvc_name':target[0]}


def workload_state(c,plan,build):
    """Read back AB rollout, immutable inputs and the exact static HDD volume bound to its claim."""
    name=plan['helm_release'];deployment=c.get('get','deployment',name,'-n','iot-features');status=deployment['status'];spec=deployment['spec']
    pod=spec['template']['spec'];api=next(v for v in pod['containers'] if v['name']=='api');values=plan['values']
    volumes=json.loads(json.dumps(pod['volumes']))
    for volume in volumes:
        if 'configMap' in volume and volume['configMap'].pop('defaultMode',420)!=420:raise ValueError('Unexpected ConfigMap file mode')
        if 'persistentVolumeClaim' in volume and volume['persistentVolumeClaim'].pop('readOnly',False):raise ValueError('Ledger must be writable')
    if (status.get('observedGeneration')!=deployment['metadata']['generation'] or status.get('replicas')!=1 or status.get('updatedReplicas')!=1
            or status.get('readyReplicas')!=1 or status.get('unavailableReplicas',0)!=0 or api['image']!=plan['image']
            or api['env']!=values['env'] or api['volumeMounts']!=values['volumeMounts']
            or pod['initContainers'][0]['image']!=plan['image'] or volumes!=values['volumes']):raise ValueError('A/B rollout/configuration differs from the tested release')
    pods=c.get('get','pods','-n','iot-features','-l','app.kubernetes.io/instance='+name)
    check_pods(pods['items'],plan['image'],build['images']['ab']['config_digest'],'api');inputs={}
    for obj in plan['objects'][:2]:
        actual=c.get('get','configmap',obj['metadata']['name'],'-n','iot-features')
        hashes={n:sha256(raw.encode()).hexdigest() for n,raw in obj['data'].items()};uid=check_seed_configmap(actual,hashes)
        inputs[obj['metadata']['name']]={'uid':uid,'sha256':hashes}
    pv=next(o for o in plan['objects'] if o['kind']=='PersistentVolume');claim=next(o for o in plan['objects'] if o['kind']=='PersistentVolumeClaim')
    actual=c.get('get','persistentvolume',pv['metadata']['name']);pvc=c.get('get','persistentvolumeclaim',claim['metadata']['name'],'-n','iot-features')
    if (actual['status']['phase']!='Bound' or pvc['status']['phase']!='Bound' or actual['spec']['claimRef']['uid']!=pvc['metadata']['uid']
            or any(actual['spec'].get(k,'' if k=='storageClassName' else None)!=v for k,v in pv['spec'].items())
            or any(pvc['spec'].get(k)!=v for k,v in claim['spec'].items())):
        raise ValueError('Experiment ledger is not bound to this release on the observed HDD')
    service=c.get('get','service',name,'-n','iot-features');url=ab_ci.private_endpoint(c.service_url(name,'iot-features'))
    return {'deployment_uid':deployment['metadata']['uid'],'generation':deployment['metadata']['generation'],'pods':pod_identity(pods),
        'inputs':inputs,'pv_uid':actual['metadata']['uid'],'pvc_uid':pvc['metadata']['uid'],'service_uid':service['metadata']['uid'],'url':url}


def offline_lookup(root,c,context):
    """Query the immutable compact table independently of Feast and expose the latest event time alongside all eight inputs."""
    import duckdb
    fields=json.loads((root/'models/pump-failure-v1/portable.json').read_text())['features']
    path=c.deploy/'data/feast/batch-medium-27/machine_rolling.parquet'
    with duckdb.connect() as connection:
        connection.execute('SET threads=1');connection.execute("SET memory_limit='1GB'")
        rows=connection.execute('SELECT machine_id,event_timestamp,'+','.join(fields)+' FROM read_parquet(?) WHERE event_timestamp<=? QUALIFY row_number() OVER(PARTITION BY machine_id ORDER BY event_timestamp DESC)=1',
            [str(path),datetime.fromisoformat(context['as_of']).replace(tzinfo=None)]).fetchall()
    if {r[0] for r in rows}!=set(context['machines']):raise ValueError('Offline cohort differs from the staged feature context')
    return {r[0]:{'feature_event_ts':r[1].replace(tzinfo=timezone.utc).isoformat(),'features':dict(zip(fields,r[2:],strict=True))} for r in rows}


def ledger_state(path,data,context):
    """Read the actual HDD database without creating it; require every HTTP trial and its complete identity seal."""
    if path.resolve().parent!=path.parent.resolve():raise ValueError('Ledger file escaped its retained HDD directory')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as connection:
        connection.execute('BEGIN')
        row=connection.execute('SELECT seal FROM identity WHERE id=1').fetchone()
        rows=[json.loads(value[0]) for value in connection.execute('SELECT response FROM trials ORDER BY id')]
    expected={'experiment':data['experiment'],'pins':{v:data[v+'_pin'] for v in ab_ci.VARIANTS},'context':context,
        'reference_id':data['reference_id'],'max_requests':2048}
    if row is None or json.loads(row[0])!=expected or rows!=data['observations']:raise ValueError('Actual retained ledger differs from HTTP trials or experiment identity')
    return {'rows':len(rows),'semantic_rows_sha256':sha256(ab_ci.canonical(rows)).hexdigest(),'seal_sha256':sha256(row[0].encode()).hexdigest()}


def run(c,root,pair,stage,build,http,text):
    """Create one isolated experiment, verify native inputs and actual traffic, then return a candidate cutover receipt."""
    import yaml
    from services.drift_api.detector import DriftDetector
    active=feature_identity(c);baseline=model_state(c,active['isvc_name']);candidate=model_state(c,pair['isvc']['metadata']['name'])
    if candidate['pin']!=pair['pin'] or candidate['isvc_uid']!=stage['isvc_uid'] or candidate['config_digest']!=build['images']['predictor']['config_digest']:
        raise ValueError('Staged candidate identity differs from the selected build')
    reference_source=c.get('get','configmap','iot-drift-reference-v1','-n','iot-features');reference_raw=reference_source['data']['reference.json']
    detector=DriftDetector(json.loads(reference_raw));context=json.loads((c.directory/'runtime/state/context.json').read_text())
    ab_ci.trial_jobs(context,c.release) # reject insufficient cohorts before creating resources
    node=c.get('get','node',baseline['node_name']);hostname=node['metadata']['labels']['kubernetes.io/hostname']
    if candidate['node_name']!=baseline['node_name']:raise ValueError('Use the observed single coursework node')
    ledger=c.directory/'runtime/ab-ledger'
    plan=ab_ci.deployment_plan(pair,baseline['pin'],baseline['url'],candidate['url'],reference_raw,
        yaml.safe_load((root/'infra/ab-api/helm-values.yaml').read_text()),str(ledger),hostname)
    for obj in plan['objects']:
        command=c.k+['get',obj['kind'],obj['metadata']['name']]
        if 'namespace' in obj['metadata']:command+=['-n',obj['metadata']['namespace']]
        if subprocess.check_output(command+['--ignore-not-found','-o','name'],text=True).strip():raise ValueError('Preserve existing A/B resources')
    existing=json.loads(subprocess.check_output([c.helm,'list','-n','iot-features','--filter','^'+plan['helm_release']+'$','-o','json'],text=True))
    if existing:raise ValueError('Preserve an existing A/B Helm release')
    ledger.mkdir(mode=0o770,parents=True,exist_ok=False);os.chown(ledger,10001,10001)
    save(c.out/'ab-plan.json',plan);save(c.out/'ab-values.json',plan['values']);save(c.out/'ab-resources.json',{'apiVersion':'v1','kind':'List','items':plan['objects']})
    c.log('ab-create',c.k+['create','-f',str(c.out/'ab-resources.json')])
    c.log('ab-lint',[c.helm,'lint',str(root/'infra/charts/iot-api'),'-f',str(c.out/'ab-values.json')])
    c.log('ab-deploy',[c.helm,'upgrade','--install',plan['helm_release'],str(root/'infra/charts/iot-api'),'-n','iot-features',
        '-f',str(c.out/'ab-values.json'),'--atomic','--wait','--timeout','240s'],600)
    initial=workload_state(c,plan,build);url=initial['url'];offline=offline_lookup(root,c,context)
    result=ab_ci.probe(lambda path,body=None,headers=None:http(url,path,body,headers),lambda path:text(url,path),
        lambda variant:text({'baseline':baseline['url'],'candidate':candidate['url']}[variant],'/metrics'),context,c.release,
        {'baseline':baseline['pin'],'candidate':candidate['pin']},detector.reference_id,offline)
    if workload_state(c,plan,build)!=initial or model_state(c,baseline['name'])!=baseline or model_state(c,candidate['name'])!=candidate or feature_identity(c)!=active:
        raise ValueError('A/B or active workload changed during the experiment')
    retained=ledger_state(ledger/'trials.sqlite',result['readback'],context)
    for name,raw in [('ab-metrics.txt',result['metrics'])]+[(f'ab-{variant}-predictor-{when}.txt',result['predictor_metrics_'+when][variant]) for variant in ab_ci.VARIANTS for when in ['before','after']]:
        (c.out/name).write_text(raw)
    save(c.out/'ab-http-probe.json',{k:v for k,v in result.items() if k not in ['readback','metrics','predictor_metrics_before','predictor_metrics_after']})
    report={**result['readback'],'passed':True,'commit':c.commit,'release':c.release,'staged_isvc_uid':candidate['isvc_uid'],
        'capture_kind':'native-kubernetes-http','baseline_native':baseline,'candidate_native':candidate,'active_feature_before':active,
        'ab_native':initial,'ab_plan_sha256':sha256((c.out/'ab-plan.json').read_bytes()).hexdigest(),
        'reference_source_uid':reference_source['metadata']['uid'],'reference_raw_sha256':plan['reference_sha256'],
        'retained_ledger':retained,
        'checked_utc':datetime.now(timezone.utc).isoformat(),'predictor_counter_deltas':result['predictor_counter_deltas'],
        'counter_scope':'Upstream counts may include health probes/other traffic; exact per-experiment counts come from the durable AB ledger/scrape.'}
    ab_gate(report,pair['pin'],stage['isvc_uid']);return report


def recheck(c,pair,stage,build,report):
    """Refuse a stale/copied receipt or changed pod, volume, input or active baseline immediately before full cutover."""
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(report['checked_utc'])).total_seconds()
    if (not 0<=age<300 or report['capture_kind']!='native-kubernetes-http' or report['commit']!=c.commit or report['release']!=c.release
            or sha256((c.out/'ab-plan.json').read_bytes()).hexdigest()!=report['ab_plan_sha256']):raise ValueError('A/B native receipt is stale or belongs to another release')
    plan=json.loads((c.out/'ab-plan.json').read_text())
    if (plan['values']!=json.loads((c.out/'ab-values.json').read_text()) or plan['config']['candidate']['pin']!=pair['pin']
            or workload_state(c,plan,build)!=report['ab_native'] or feature_identity(c)!=report['active_feature_before']
            or model_state(c,report['baseline_native']['name'])!=report['baseline_native']
            or model_state(c,pair['isvc']['metadata']['name'])!=report['candidate_native']):raise ValueError('Actual A/B inputs/workloads changed before cutover')
    context=json.loads((c.directory/'runtime/state/context.json').read_text())
    if ledger_state(c.directory/'runtime/ab-ledger/trials.sqlite',report,context)!=report['retained_ledger']:raise ValueError('Retained ledger changed before cutover')
    ab_gate(report,pair['pin'],stage['isvc_uid'])
