"""Render isolated real model6 and identity probe deployments for native service-mesh authorization checks."""
import re

NAMESPACE='iot-mesh'
IMAGE='iot-coursework-predictor:observe-jenkins-2'


def resources():
    """Keep production namespaces unchanged and expose only an internal HTTP service protected by strict mesh policies."""
    result=[{'apiVersion':'v1','kind':'Namespace','metadata':{'name':NAMESPACE,'labels':{'istio.io/rev':'iot-1313'}}}]
    for name in ['model-server','feature-caller','untrusted-caller','plaintext-caller']:result.append({'apiVersion':'v1','kind':'ServiceAccount','metadata':{'name':name,'namespace':NAMESPACE}})
    for name,identity,inject in [('mesh-predictor','model-server',True),('allowed-client','feature-caller',True),('denied-client','untrusted-caller',True),('plain-client','plaintext-caller',False)]:
        server=name=='mesh-predictor';container={'name':'application','image':IMAGE,'imagePullPolicy':'Never','command':['python','-m','uvicorn','services.predictor.app:create_app','--factory','--host','0.0.0.0','--port','8080'] if server else ['python','-c','import time;time.sleep(10800)'],
            'env':[{'name':'PYTHONDONTWRITEBYTECODE','value':'1'}],'resources':{'requests':{'cpu':'10m','memory':'64Mi'},'limits':{'cpu':'500m','memory':'256Mi' if server else '128Mi'}},
            'securityContext':{'runAsUser':10001,'runAsNonRoot':True,'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}},'volumeMounts':[{'name':'tmp','mountPath':'/tmp'}]}
        if server:container.update({'ports':[{'name':'http','containerPort':8080}],'readinessProbe':{'httpGet':{'path':'/health/ready','port':'http'},'periodSeconds':3}})
        result.append({'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':name,'namespace':NAMESPACE},'spec':{'replicas':1,'selector':{'matchLabels':{'app':name}},'template':{'metadata':{'labels':{'app':name},'annotations':{'sidecar.istio.io/inject':str(inject).lower(),'sidecar.istio.io/rewriteAppHTTPProbers':'true'}},'spec':{'serviceAccountName':identity,'securityContext':{'seccompProfile':{'type':'RuntimeDefault'}},'containers':[container],'volumes':[{'name':'tmp','emptyDir':{'medium':'Memory','sizeLimit':'16Mi'}}]}}}})
    result.append({'apiVersion':'v1','kind':'Service','metadata':{'name':'mesh-predictor','namespace':NAMESPACE},'spec':{'type':'ClusterIP','selector':{'app':'mesh-predictor'},'ports':[{'name':'http','port':8080,'targetPort':8080}]}})
    return result
