"""Bind a production serving release, paired endpoints and native verification to one trusted CI build."""
from copy import deepcopy
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import PurePosixPath
import base64,json,math,re,tarfile
from services.predictor.binding import FIELDS,validate_pin

MUTATION_FILES={'services/feature_api/app.py','services/feature_api/runtime.py',
    'services/predictor/runtime.py','services/predictor/binding.py'}


def host_source_path(source,deploy):
    """Translate the existing Jenkins home bind mount into the Docker daemon's host filesystem."""
    source=PurePosixPath(str(source));deploy=PurePosixPath(str(deploy))
    if not source.is_absolute() or not deploy.is_absolute() or '..' in source.parts+deploy.parts:raise ValueError('Use absolute native source paths')
    if source.is_relative_to('/var/jenkins_home'):
        return deploy/'data/runtime/jenkins-home-27'/source.relative_to('/var/jenkins_home')
    return source


def identity(build,commit,version):
    """Validate operator-supplied identities before using them in paths, tags or Kubernetes names."""
    if not isinstance(build,str) or not re.fullmatch(r'[1-9][0-9]{0,9}',build):raise ValueError('Invalid Jenkins build number')
    if not isinstance(commit,str) or not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('Pin the full source commit')
    if not isinstance(version,str) or not re.fullmatch('[1-9][0-9]*',version):raise ValueError('Pin a canonical model version')
    return 'serve-jenkins-'+build


def admission(stop,now,ram,disks,required_seconds=3600):
    """Reserve a full hour initially for this bounded CI; never alter the separate full-training guards."""
    deadline=datetime.fromisoformat(stop.replace('Z','+00:00'))
    if deadline.tzinfo is None or now.tzinfo is None:raise ValueError('Use aware audit clocks')
    seconds=(deadline-now).total_seconds()
    if seconds<required_seconds or ram<12*1024**3 or len(disks)!=2 or min(disks)<20_000_000_000:
        raise ValueError('Serving CI needs its actual time reserve,12GiB RAM and20GB on both data disks')
    return {'passed':True,'seconds_before_actual_stop':seconds,'required_seconds':required_seconds,
        'ram_available_bytes':ram,'disk_free_bytes':disks,'auto_stop_utc':stop,'checked_utc':now.astimezone(timezone.utc).isoformat()}


def test_gate(coverage,stats,all_results):
    """Require strict coverage>90/mutation>80, all mutants accounted for, and only the four changed API modules."""
    c=coverage['totals']
    if (c['num_statements']<=0 or c['num_branches']<=0 or c['excluded_lines']!=0
            or c['covered_lines']/c['num_statements']<=.9 or c['covered_branches']/c['num_branches']<=.9):
        raise ValueError('Serving API statement and branch coverage must exceed90% without exclusions')
    keys=['killed','survived','no_tests','skipped','suspicious','timeout','check_was_interrupted_by_user','segfault']
    if any(type(stats.get(k)) is not int or stats[k]<0 for k in keys+['total']):raise ValueError('Incomplete native mutation counts')
    if stats['total']<=0 or sum(stats[k] for k in keys)!=stats['total'] or any(stats[k] for k in keys[2:]):
        raise ValueError('Every mutant must finish as killed or survived; no timeout/skip/interruption credit')
    entries=re.findall(r'^\s*(\S+): (killed|survived)\s*$',all_results,re.M)
    if len(entries)!=stats['total'] or len({name for name,_ in entries})!=len(entries):raise ValueError('All-mutant result inventory differs')
    files={name.split('.x_',1)[0].replace('.','/')+'.py' for name,_ in entries}
    if files!=MUTATION_FILES or sum(status=='killed' for _,status in entries)!=stats['killed']:
        raise ValueError('Mutation results do not match the changed API modules/counts')
    score=stats['killed']/stats['total']
    if score<=.8:raise ValueError('Serving mutation score must exceed80%')
    return {'passed':True,'coverage':c,'mutation':stats,'mutation_score_percent':100*score,'mutation_files':sorted(files)}


def docker_config_digest(stream):
    """Hash the actual unique Docker-save config blob rather than assuming manifest ID equals CRI config ID."""
    blobs={};manifest=None
    with tarfile.open(fileobj=stream,mode='r|') as archive:
        for item in archive:
            if not item.isfile() or item.size>1_000_000:continue
            raw=archive.extractfile(item).read();blobs[item.name]=sha256(raw).hexdigest()
            if item.name=='manifest.json':manifest=json.loads(raw)
    if not isinstance(manifest,list) or len(manifest)!=1 or manifest[0].get('Config') not in blobs:
        raise ValueError('Image export lacks a unique bounded config blob')
    return 'sha256:'+blobs[manifest[0]['Config']]


def deployment_plan(build,commit,pin,isvc,redis,values):
    """Stage a separate KServe/Redis pair; only the feature Helm switch changes the active endpoint."""
    pin=validate_pin(pin);release=identity(build,commit,pin['version'])
    if pin['production'] is not True:raise ValueError('CI must pull a production-marked model')
    model=deepcopy(isvc);online=deepcopy(redis);feature=deepcopy(values)
    model_name='iot-pump-'+release;redis_name='online-'+release;seed='iot-feature-state-'+release
    model['metadata']['name']=model_name;model['metadata']['namespace']='iot-serving'
    model['metadata'].setdefault('labels',{})['iot.coursework/release']=release
    annotations=model['metadata'].setdefault('annotations',{})
    annotations.update({'coursework.iot/model-portable-sha256':pin['portable_sha256'],
        'coursework.iot/source-commit':commit,'coursework.iot/model-version':pin['version']})
    predictor_tag='iot-coursework-predictor:'+release;feature_tag='iot-coursework-feature-api:'+release
    model['spec']['predictor']['containers'][0]['image']=predictor_tag
    for obj in online:
        obj['metadata']['name']=redis_name;obj['metadata']['namespace']='iot-features'
        obj['metadata'].setdefault('labels',{})['iot.coursework/release']=release
        if obj['kind']=='Deployment':
            obj['spec']['selector']['matchLabels']={'app':redis_name}
            obj['spec']['template']['metadata']['labels']={'app':redis_name,'iot.coursework/release':release}
        elif obj['kind']=='Service':obj['spec']['selector']={'app':redis_name};obj['spec']['type']='ClusterIP'
        else:raise ValueError('Stage only the dedicated Redis Deployment/Service')
    endpoint='http://'+model_name+'-predictor.iot-serving.svc.cluster.local'
    feature['image'].update(repository='iot-coursework-feature-api',tag=release,pullPolicy='Never')
    feature['revisionMarker']=release;feature['autoscaling']={'enabled':True,'minReplicas':1,'maxReplicas':2,'targetCPUUtilizationPercentage':60}
    feature['initContainers'][0]['image']=feature_tag
    feature['env']=[{'name':'IOT_FEAST_REDIS','value':redis_name+'.iot-features.svc.cluster.local:6379'},
        {'name':'IOT_SERVING_STATE','value':'/state'},{'name':'IOT_KSERVE_URL','value':endpoint},{'name':'IOT_MODEL_PIN','value':'/app/model-pin.json'}]
    feature['volumes'][0]['configMap']['name']=seed
    return {'release':release,'commit':commit,'pin':pin,'predictor_image':predictor_tag,'feature_image':feature_tag,
        'isvc':model,'redis':online,'feature_values':feature,'seed_configmap':seed,'predictor_url':endpoint}


def check_prediction(prediction,metadata,pin):
    """Reject a different trained model or inconsistent decision, including bools masquerading as numeric values."""
    expected=validate_pin(pin)
    if type(metadata.get('production')) is not bool or any(metadata.get(n)!=expected[n] for n in FIELDS):raise ValueError('HTTP model identity differs')
    score=prediction['risk_score'];decision=prediction['predicted_failure']
    if (type(score) not in [float,int] or not math.isfinite(score) or not 0<=score<=1
            or type(decision) is not int or decision!=int(score>=expected['threshold'])):raise ValueError('Invalid HTTP model decision')


def check_seed_configmap(value,hashes,uid=None):
    """Verify all four immutable seed files and retain the exact native ConfigMap identity."""
    data=value.get('data',{});binary=value.get('binaryData',{})
    if value.get('immutable') is not True or set(data)&set(binary) or set(data)|set(binary)!=set(hashes):raise ValueError('Seed ConfigMap schema or immutability differs')
    if uid is not None and value['metadata']['uid']!=uid:raise ValueError('Seed ConfigMap was replaced')
    for name,digest in hashes.items():
        raw=data[name].encode() if name in data else base64.b64decode(binary[name],validate=True)
        if sha256(raw).hexdigest()!=digest:raise ValueError('Native seed bytes differ: '+name)
    return value['metadata']['uid']


def check_pods(pods,image,config,container):
    """Require actual Ready containers using the imported config digest; ignore only pods already terminating."""
    live=[p for p in pods if not p['metadata'].get('deletionTimestamp')]
    if not live:raise ValueError('No live deployment pods')
    for pod in live:
        spec=next(c for c in pod['spec']['containers'] if c['name']==container)
        status=next(s for s in pod['status'].get('containerStatuses',[]) if s['name']==container)
        actual=status.get('imageID','').split('://')[-1].split('@')[-1]
        if spec['image']!=image or not status.get('ready') or actual!=config or 'running' not in status.get('state',{}):
            raise ValueError('Native pod image/readiness differs from the verified build')
    return [p['metadata']['uid'] for p in live]


def check_deployment(deployment,plan):
    """Require a completed Helm rollout whose API and init container point to the same tested release."""
    status=deployment['status'];spec=deployment['spec'];template=spec['template']
    if (status.get('observedGeneration')!=deployment['metadata']['generation'] or status.get('replicas',0)<1
            or status.get('updatedReplicas')!=status['replicas'] or status.get('readyReplicas')!=status['replicas']
            or status.get('unavailableReplicas',0)!=0):raise ValueError('Feature rollout is incomplete')
    api=next(c for c in template['spec']['containers'] if c['name']=='api')
    if (api['image']!=plan['feature_image'] or template['spec']['initContainers'][0]['image']!=plan['feature_image']
            or {v['name']:v.get('value') for v in api['env']}!={v['name']:v['value'] for v in plan['feature_values']['env']}
            or template['spec']['volumes'][0]['configMap']['name']!=plan['seed_configmap']):
        raise ValueError('Feature deployment does not use the paired model/Redis/seed')
