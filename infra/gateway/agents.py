"""Extend the retained private NGINX controller with authenticated, rate-limited agent and catalog routes."""
from copy import deepcopy
import re

NAMESPACE='iot-agents'
ROUTES={'agents':('agents.iot.test','kagent-ui',8080),'registry':('registry.iot.test','iot-agent-registry',12121)}
WATCH='-watch-namespace=iot-gateway,iot-features,iot-observability'


def controller_patch(deployment,image):
    """Use UID and argument tests to add exactly one watched namespace without replacing other deployment settings."""
    if not re.fullmatch(r'nginx/nginx-ingress@sha256:[a-f0-9]{64}',image):raise ValueError('Pin the existing official controller image')
    metadata=deployment['metadata'];spec=deployment['spec'];containers=spec['template']['spec']['containers']
    if (metadata.get('name')!='iot-gateway' or metadata.get('namespace')!='iot-gateway' or not metadata.get('uid')
            or spec['selector']['matchLabels']!={'app':'iot-gateway'} or len(containers)!=1
            or containers[0].get('name')!='controller' or containers[0].get('image')!=image):
        raise ValueError('Preserve a different gateway deployment or controller image')
    arguments=containers[0]['args'];watched=[v for v in arguments if v.startswith('-watch-namespace=')]
    if watched==[WATCH+',iot-agents']:return []
    if watched!=[WATCH]:raise ValueError('Inspect an unexpected gateway namespace scope')
    replacement=deepcopy(arguments);replacement[replacement.index(WATCH)]=WATCH+',iot-agents'
    return [{'op':'test','path':'/metadata/uid','value':metadata['uid']},
            {'op':'test','path':'/spec/template/spec/containers/0/args','value':arguments},
            {'op':'replace','path':'/spec/template/spec/containers/0/args','value':replacement}]


def access_resources():
    """Grant the existing controller read-only discovery in the agent namespace, with only ingress-status/event writes."""
    return [
        {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'Role','metadata':{'name':'iot-agent-gateway-read','namespace':NAMESPACE},'rules':[
            {'apiGroups':[''],'resources':['services','secrets','configmaps','pods'],'verbs':['get','list','watch']},
            {'apiGroups':['discovery.k8s.io'],'resources':['endpointslices'],'verbs':['get','list','watch']},
            {'apiGroups':['networking.k8s.io'],'resources':['ingresses'],'verbs':['get','list','watch']},
            {'apiGroups':['networking.k8s.io'],'resources':['ingresses/status'],'verbs':['update']},
            {'apiGroups':[''],'resources':['events'],'verbs':['create','patch','list']}]},
        {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'RoleBinding','metadata':{'name':'iot-agent-gateway-read','namespace':NAMESPACE},
         'subjects':[{'kind':'ServiceAccount','name':'iot-gateway','namespace':'iot-gateway'}],
         'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':'iot-agent-gateway-read'}}]


def ingresses():
    """Use fixed private hosts and existing ClusterIP backends; require Basic auth, TLS and bounded request admission."""
    result=[]
    for name,(host,service,port) in ROUTES.items():
        annotations={'nginx.org/basic-auth-secret':'agent-gateway-auth','nginx.org/basic-auth-realm':'IoT agent lab',
            'nginx.org/ssl-redirect':'true','nginx.org/proxy-set-headers':'Authorization:,X-User-Id: $remote_user,X-Forwarded-User: $remote_user',
            'nginx.org/proxy-read-timeout':'660s','nginx.org/proxy-send-timeout':'660s','nginx.org/proxy-buffering':'false',
            'nginx.org/limit-req-rate':'30r/s','nginx.org/limit-req-key':'${binary_remote_addr}',
            'nginx.org/limit-req-burst':'90','nginx.org/limit-req-no-delay':'true','nginx.org/limit-req-reject-code':'429'}
        result.append({'apiVersion':'networking.k8s.io/v1','kind':'Ingress','metadata':{'name':'iot-agent-'+name,'namespace':NAMESPACE,'annotations':annotations},
            'spec':{'ingressClassName':'iot-gateway','tls':[{'hosts':[host],'secretName':'agent-gateway-tls'}],
                'rules':[{'host':host,'http':{'paths':[{'path':'/','pathType':'Prefix','backend':{'service':{'name':service,'port':{'number':port}}}}]}}]}})
    return result
