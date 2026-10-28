"""Constrain a telemetry-only CI release to the existing production model, private receiver and exact application bytes."""
from copy import deepcopy
from hashlib import sha256
import ipaddress,re
from services.predictor.binding import FIELDS,validate_pin

ENDPOINT='http://iot-jaeger.iot-observability.svc.cluster.local:4318/v1/traces'
KINDS=('feature','drift','predictor')


def registry_address(container):
    """Discover exactly one existing owned private registry network, including a restored bridge after VM restart."""
    networks=container['NetworkSettings']['Networks'];mode=container['HostConfig']['NetworkMode']
    if (len(networks)!=1 or mode not in networks or (mode!='iot-mlflow_default' and re.fullmatch(r'iot-mlflow-restored-[a-z0-9-]+',mode) is None)):
        raise ValueError('Require one observed coursework registry network')
    address=ipaddress.ip_address(networks[mode]['IPAddress'])
    if address.version!=4 or not address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
        raise ValueError('Registry must remain private')
    return str(address)


def release(build):
    """Use a separate namespace so telemetry job numbers cannot overwrite model-serving releases."""
    if not isinstance(build,str) or re.fullmatch(r'[1-9][0-9]{0,9}',build) is None:raise ValueError('Use a canonical build number')
    return 'observe-jenkins-'+build


def environment(env,kind):
    """Add only fixed instrumentation settings while preserving the actual model/store configuration."""
    if kind not in KINDS or len({v['name'] for v in env})!=len(env):raise ValueError('Unknown service or duplicate environment names')
    result=deepcopy(env)
    for name,value in [('IOT_API_SERVICE',kind),('IOT_TRACE_ENDPOINT',ENDPOINT)]:
        old=next((v for v in result if v['name']==name),None)
        if old is not None:
            if old!={'name':name,'value':value}:raise ValueError('Preserve unexpected instrumentation configuration')
        else:result.append({'name':name,'value':value})
    return result


def same_model(pin,actual,portable):
    """Permit instrumentation of exactly the current production version; a model change needs the serving/A-B pipeline."""
    pin=validate_pin(pin)
    if pin['production'] is not True or type(actual.get('production')) is not bool or any(actual.get(n)!=pin[n] for n in FIELDS):
        raise ValueError('Telemetry CI cannot change the production model')
    if sha256(portable).hexdigest()!=pin['portable_sha256']:raise ValueError('Registry weights differ from the current model')
    return pin


def application_paths(kind,files):
    """Declare every Python module of the selected business API and its packaged shared dependencies."""
    if kind not in KINDS:raise ValueError('Unknown API kind')
    directory={'feature':'feature_api','drift':'drift_api','predictor':'predictor'}[kind]
    names={n for n in files if n.startswith('services/'+directory+'/') and n.endswith('.py')}
    if len(names)<3:raise ValueError('Incomplete business source inventory')
    names.add('services/__init__.py')
    if kind=='feature':names|={'services/predictor/'+n+'.py' for n in ['__init__','contracts','binding']}|{'ml/__init__.py','ml/protocol.py'}
    if kind=='drift':names|={'services/predictor/__init__.py','services/predictor/contracts.py'}
    if kind=='predictor':names|={'ml/__init__.py','ml/protocol.py','ml/training_contract.py'}
    if not names<=files.keys():raise ValueError('Shared application module is missing')
    return {n:'/app/'+n for n in sorted(names)}


def patch_predictor(obj,image,env):
    """Guard the actual Kubernetes identity, current image and environment before changing only instrumentation."""
    current=obj['spec']['predictor']['containers'][0]
    if not re.fullmatch(r'iot-coursework-predictor:observe-jenkins-[1-9][0-9]{0,9}',image):raise ValueError('Use this pipeline image namespace')
    patches=[{'op':'test','path':'/metadata/uid','value':obj['metadata']['uid']},
             {'op':'test','path':'/metadata/resourceVersion','value':obj['metadata']['resourceVersion']},
             {'op':'test','path':'/spec/predictor/containers/0/image','value':current['image']}]
    if 'env' in current:patches.append({'op':'test','path':'/spec/predictor/containers/0/env','value':current['env']})
    return patches+[{'op':'replace','path':'/spec/predictor/containers/0/image','value':image},
                    {'op':'add','path':'/spec/predictor/containers/0/env','value':environment(env,'predictor')}]
