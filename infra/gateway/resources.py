"""Render a bounded private NGINX gateway for existing monitoring and feature services."""
from ipaddress import ip_address
import re

NAMESPACE = 'iot-gateway'
HOSTS = {'metrics': 'metrics.iot.test', 'logs': 'logs.iot.test', 'traces': 'traces.iot.test', 'features': 'features.iot.test'}


def resources(image, grafana_ip):
    """Render only private services; runtime TLS/auth values are supplied separately as Secrets."""
    if not re.fullmatch(r'nginx/nginx-ingress@sha256:[0-9a-f]{64}', image):
        raise ValueError('Require a resolved official controller image digest')
    for value in [grafana_ip]:
        address = ip_address(value)
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast:
            raise ValueError('Only explicit private IPv4 upstreams are allowed')
    obj = lambda kind, name, **kw: {'apiVersion': 'v1', 'kind': kind, 'metadata': {'name': name, 'namespace': NAMESPACE}, **kw}
    role = obj('Role', 'iot-gateway', rules=[
        {'apiGroups': [''], 'resources': ['services', 'secrets', 'configmaps', 'pods'], 'verbs': ['get', 'list', 'watch']},
        {'apiGroups': ['discovery.k8s.io'], 'resources': ['endpointslices'], 'verbs': ['get', 'list', 'watch']},
        {'apiGroups': ['networking.k8s.io'], 'resources': ['ingresses'], 'verbs': ['get', 'list', 'watch']},
        {'apiGroups': ['networking.k8s.io'], 'resources': ['ingresses/status'], 'verbs': ['update']},
        {'apiGroups': [''], 'resources': ['events'], 'verbs': ['create', 'patch', 'list']},
    ]);role['apiVersion'] = 'rbac.authorization.k8s.io/v1'
    cluster = {'apiVersion': role['apiVersion'], 'kind': 'ClusterRole', 'metadata': {'name': 'iot-gateway-class-reader'}, 'rules': [
        {'apiGroups': ['networking.k8s.io'], 'resources': ['ingressclasses'], 'verbs': ['get', 'list', 'watch']},
        {'apiGroups': [''], 'resources': ['namespaces'], 'verbs': ['get', 'list', 'watch']},
    ]}
    sa = {'kind': 'ServiceAccount', 'name': 'iot-gateway', 'namespace': NAMESPACE}
    bind = lambda kind, name, target: {'apiVersion': role['apiVersion'], 'kind': kind, 'metadata': {'name': name, **({'namespace': NAMESPACE} if kind == 'RoleBinding' else {})}, 'subjects': [sa], 'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': target[0], 'name': target[1]}}
    deployment = obj('Deployment', 'iot-gateway', spec={'replicas': 1, 'selector': {'matchLabels': {'app': 'iot-gateway'}}, 'template': {'metadata': {'labels': {'app': 'iot-gateway'}}, 'spec': {
        'serviceAccountName': 'iot-gateway', 'securityContext': {'seccompProfile': {'type': 'RuntimeDefault'}}, 'containers': [{
            'name': 'controller', 'image': image, 'imagePullPolicy': 'IfNotPresent',
            'args': ['-ingress-class=iot-gateway', '-watch-namespace=iot-gateway,iot-features,iot-observability', '-enable-custom-resources=false', '-enable-leader-election=false', '-enable-telemetry-reporting=false', '-nginx-configmaps=iot-gateway/nginx-config', '-nginx-status=false'],
            'env': [{'name': 'POD_NAMESPACE', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.namespace'}}}, {'name': 'POD_NAME', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.name'}}}],
            'ports': [{'name': 'http', 'containerPort': 80}, {'name': 'https', 'containerPort': 443}, {'name': 'ready', 'containerPort': 8081}],
            'readinessProbe': {'httpGet': {'path': '/nginx-ready', 'port': 'ready'}, 'periodSeconds': 2},
            'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '1', 'memory': '512Mi'}},
            'securityContext': {'allowPrivilegeEscalation': False, 'runAsUser': 101, 'runAsNonRoot': True, 'capabilities': {'drop': ['ALL'], 'add': ['NET_BIND_SERVICE']}}
        }]}}});deployment['apiVersion'] = 'apps/v1'
    result = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NAMESPACE}}, obj('ServiceAccount', 'iot-gateway'), role, cluster, bind('RoleBinding', 'iot-gateway', ('Role', 'iot-gateway')), bind('ClusterRoleBinding', 'iot-gateway-class-reader', ('ClusterRole', 'iot-gateway-class-reader')),
        {'apiVersion': 'networking.k8s.io/v1', 'kind': 'IngressClass', 'metadata': {'name': 'iot-gateway'}, 'spec': {'controller': 'nginx.org/ingress-controller'}},
        obj('ConfigMap', 'nginx-config', data={'ssl-protocols': 'TLSv1.2 TLSv1.3', 'ssl-redirect': 'true', 'http-redirect-code': '308', 'server-tokens': 'false', 'worker-processes': '1', 'log-format-escaping': 'json', 'log-format': '$time_iso8601 $host $status $request_method $uri'}),
        deployment, obj('Service', 'iot-gateway', spec={'type': 'ClusterIP', 'selector': {'app': 'iot-gateway'}, 'ports': [{'name': 'http', 'port': 80, 'targetPort': 'http'}, {'name': 'https', 'port': 443, 'targetPort': 'https'}]})]
    # Existing services keep EndpointSlice discovery in their own namespaces.
    for ns in ['iot-features', 'iot-observability']:
        import copy
        scoped = copy.deepcopy(role);scoped['metadata']['namespace'] = ns;result.append(scoped)
        binding = bind('RoleBinding', 'iot-gateway', ('Role', 'iot-gateway'));binding['metadata']['namespace'] = ns;result.append(binding)
    for name, ns, service, port in [('metrics', NAMESPACE, 'metrics', 13002), ('logs', NAMESPACE, 'logs', 13002), ('traces', 'iot-observability', 'iot-jaeger', 16686), ('features', 'iot-features', 'iot-feature-api', 80)]:
        if ns == NAMESPACE:
            result.append(obj('Service', name, spec={'type': 'ClusterIP', 'ports': [{'name': 'http', 'port': port, 'targetPort': port}]}))
            result.append({'apiVersion': 'discovery.k8s.io/v1', 'kind': 'EndpointSlice', 'metadata': {'name': name, 'namespace': NAMESPACE, 'labels': {'kubernetes.io/service-name': name, 'endpointslice.kubernetes.io/managed-by': 'iot-coursework'}}, 'addressType': 'IPv4', 'ports': [{'name': 'http', 'protocol': 'TCP', 'port': port}], 'endpoints': [{'addresses': [grafana_ip], 'conditions': {'ready': True}}]})
        annotations = {'nginx.org/basic-auth-secret': 'gateway-auth', 'nginx.org/basic-auth-realm': 'IoT coursework', 'nginx.org/ssl-redirect': 'true'}
        if name == 'features':
            annotations.update({'nginx.org/limit-req-rate': '5r/s', 'nginx.org/limit-req-key': '${binary_remote_addr}', 'nginx.org/limit-req-burst': '5', 'nginx.org/limit-req-no-delay': 'true', 'nginx.org/limit-req-reject-code': '429'})
        result.append({'apiVersion': 'networking.k8s.io/v1', 'kind': 'Ingress', 'metadata': {'name': name, 'namespace': ns, 'annotations': annotations}, 'spec': {'ingressClassName': 'iot-gateway', 'tls': [{'hosts': [HOSTS[name]], 'secretName': 'gateway-tls'}], 'rules': [{'host': HOSTS[name], 'http': {'paths': [{'path': '/', 'pathType': 'Prefix', 'backend': {'service': {'name': service, 'port': {'number': port}}}}]}}]}})
    return result
