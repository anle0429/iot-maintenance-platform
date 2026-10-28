"""Build private Broker-to-KServe drift resources and bounded binary CloudEvents without changing the reference cohort."""
from hashlib import sha256
from pathlib import PurePosixPath
import ipaddress,json,re
from services.predictor.binding import validate_pin

EVENT_TYPE='dev.edai.iot.drift.window'
NAMESPACE='iot-drift-events'


def private_services(items,resolve):
    """Allow an internal KServe/channel DNS alias only after resolving it to a private ClusterIP service."""
    result={}
    for service in items:
        spec=service['spec'];kind=spec.get('type','ClusterIP');target=service
        if spec.get('externalIPs') or service.get('status',{}).get('loadBalancer',{}).get('ingress'):raise ValueError('No public Eventing service')
        if kind=='ExternalName':
            parts=spec['externalName'].split('.')
            if len(parts)!=5 or parts[2:]!=['svc','cluster','local'] or parts[1] not in [NAMESPACE,'knative-eventing']:raise ValueError('Alias leaves the owned cluster namespaces')
            target=resolve(parts[0],parts[1]);spec=target['spec'];kind=spec.get('type','ClusterIP')
        if kind!='ClusterIP' or spec.get('externalIPs'):raise ValueError('Sink must resolve to ClusterIP')
        address=ipaddress.ip_address(spec['clusterIP'])
        if address.version!=4 or not address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:raise ValueError('Sink address is not private')
        result[service['metadata']['name']]={'target':target['metadata']['name'],'namespace':target['metadata']['namespace'],'cluster_ip':str(address)}
    return result


def event_headers(identity):
    """Use fixed CloudEvent routing attributes and reject caller-controlled header injection."""
    if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,100}',identity):raise ValueError('Invalid event identity')
    return {'Content-Type':'application/json','ce-specversion':'1.0','ce-type':EVENT_TYPE,'ce-source':'urn:edai:iot:window-publisher','ce-id':identity}


def plan(release,image,node,reference_raw,pin,portable,ledger):
    """Require the production model's exact data version/schema and a separate retained local drift ledger."""
    if not re.fullmatch(r'drift-event-(?:jenkins-[1-9][0-9]{0,9}|pilot-28)',release):raise ValueError('Invalid owned release name')
    if not re.fullmatch(r'iot-coursework-drift-api:[a-z0-9][a-z0-9.-]*',image):raise ValueError('Use an inspected local drift image')
    pin=validate_pin(pin);reference=json.loads(reference_raw)
    if (pin['production'] is not True or reference['lineage']['partition']!='train'
            or reference['lineage']['data_version']!=pin['data_version'] or reference['lineage']['protocol_sha256']!=pin['protocol_sha256']
            or not 32<=len(reference['samples'])<=2048 or any(set(row)!=set(portable['features']) for row in reference['samples'])):
        raise ValueError('Production schema/data lineage and fixed training reference differ')
    path=PurePosixPath(ledger)
    if not path.is_relative_to('/srv/iot-data') or '..' in path.parts or path.parts[-2:]!=('eventing',release):raise ValueError('Keep a separate owned HDD ledger')
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,62}',node):raise ValueError('Use the observed node hostname')
    name='iot-'+release;pv=name+'-data';cm=name+'-reference';broker=name+'-broker'
    labels={'iot.coursework/release':release}
    objects=[{'apiVersion':'v1','kind':'Namespace','metadata':{'name':NAMESPACE,'labels':{'pod-security.kubernetes.io/enforce':'restricted'}}},
        {'apiVersion':'v1','kind':'ServiceAccount','metadata':{'name':name,'namespace':NAMESPACE,'labels':labels},'automountServiceAccountToken':False},
        {'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':cm,'namespace':NAMESPACE,'labels':labels},'immutable':True,'data':{'reference.json':reference_raw}},
        {'apiVersion':'v1','kind':'PersistentVolume','metadata':{'name':pv,'labels':labels},'spec':{'capacity':{'storage':'1Gi'},'accessModes':['ReadWriteOnce'],'persistentVolumeReclaimPolicy':'Retain','storageClassName':'','hostPath':{'path':str(path),'type':'Directory'},'claimRef':{'name':pv,'namespace':NAMESPACE},'nodeAffinity':{'required':{'nodeSelectorTerms':[{'matchExpressions':[{'key':'kubernetes.io/hostname','operator':'In','values':[node]}]}]}}}},
        {'apiVersion':'v1','kind':'PersistentVolumeClaim','metadata':{'name':pv,'namespace':NAMESPACE,'labels':labels},'spec':{'accessModes':['ReadWriteOnce'],'storageClassName':'','volumeName':pv,'resources':{'requests':{'storage':'1Gi'}}}},
        {'apiVersion':'serving.kserve.io/v1beta1','kind':'InferenceService','metadata':{'name':name,'namespace':NAMESPACE,'labels':labels,'annotations':{'serving.kserve.io/deploymentMode':'Standard','sidecar.istio.io/inject':'false','coursework.iot/production-portable-sha256':pin['portable_sha256'],'coursework.iot/reference-bytes-sha256':sha256(reference_raw.encode()).hexdigest()}},
         'spec':{'predictor':{'minReplicas':1,'maxReplicas':1,'serviceAccountName':name,'securityContext':{'runAsNonRoot':True,'runAsUser':10001,'runAsGroup':10001,'fsGroup':10001,'seccompProfile':{'type':'RuntimeDefault'}},
            'volumes':[{'name':'reference','configMap':{'name':cm}},{'name':'state','persistentVolumeClaim':{'claimName':pv}},{'name':'tmp','emptyDir':{'medium':'Memory','sizeLimit':'32Mi'}}],
            'containers':[{'name':'kserve-container','image':image,'imagePullPolicy':'Never','ports':[{'containerPort':8080,'name':'http1'}],
             'env':[{'name':'IOT_DRIFT_REFERENCE','value':'/reference/reference.json'},{'name':'IOT_DRIFT_DATABASE','value':'/state/windows.db'},{'name':'IOT_API_SERVICE','value':'drift'},{'name':'IOT_TRACE_ENDPOINT','value':'http://iot-jaeger.iot-observability.svc.cluster.local:4318/v1/traces'}],
             'resources':{'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'cpu':'500m','memory':'512Mi'}},'securityContext':{'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}},
             'volumeMounts':[{'name':'reference','mountPath':'/reference','readOnly':True},{'name':'state','mountPath':'/state'},{'name':'tmp','mountPath':'/tmp'}],
             'startupProbe':{'httpGet':{'path':'/health/ready','port':8080},'periodSeconds':2,'failureThreshold':60},'readinessProbe':{'httpGet':{'path':'/health/ready','port':8080},'periodSeconds':2},'livenessProbe':{'httpGet':{'path':'/health/live','port':8080},'periodSeconds':10}}]}}},
        {'apiVersion':'eventing.knative.dev/v1','kind':'Broker','metadata':{'name':broker,'namespace':NAMESPACE,'labels':labels,'annotations':{'eventing.knative.dev/broker.class':'MTChannelBasedBroker'}},'spec':{'delivery':{'retry':3,'backoffPolicy':'exponential','backoffDelay':'PT1S'}}},
        {'apiVersion':'eventing.knative.dev/v1','kind':'Trigger','metadata':{'name':name+'-trigger','namespace':NAMESPACE,'labels':labels},'spec':{'broker':broker,'filter':{'attributes':{'type':EVENT_TYPE}},'subscriber':{'uri':'http://'+name+'-predictor.'+NAMESPACE+'.svc.cluster.local/v1/events/drift'}}}]
    return {'release':release,'namespace':NAMESPACE,'name':name,'broker':broker,'ledger':str(path),'objects':objects,'model_pin':pin,'reference_lineage':reference['lineage']}
