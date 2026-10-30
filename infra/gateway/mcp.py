"""Describe one private retrieval route without embedding an MCP token or changing existing ingress resources."""
NAMESPACE = 'iot-agents'
HOST = 'retrieval.iot.test'
SERVICE = 'iot-retrieval-mcp'
UPSTREAM_HOST = SERVICE + '.' + NAMESPACE + '.svc.cluster.local'


def resources():
    """Allow only the retained gateway Pods and keep edge Basic authentication separate from upstream bearer auth."""
    return [
        {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
         'metadata': {'name': 'iot-mcp-gateway', 'namespace': NAMESPACE},
         'spec': {'podSelector': {'matchLabels': {'app.kubernetes.io/instance': SERVICE}},
                  'policyTypes': ['Ingress'], 'ingress': [
                      {'from': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'iot-gateway'}},
                                 'podSelector': {'matchLabels': {'app': 'iot-gateway'}}}],
                       'ports': [{'protocol': 'TCP', 'port': 8080}]}]}},
        {'apiVersion': 'networking.k8s.io/v1', 'kind': 'Ingress',
         'metadata': {'name': 'iot-mcp-retrieval', 'namespace': NAMESPACE, 'annotations': {
             'nginx.org/basic-auth-secret': 'agent-gateway-auth',
             'nginx.org/basic-auth-realm': 'IoT retrieval lab',
             'nginx.org/ssl-redirect': 'true',
             'nginx.org/upstream-vhost': UPSTREAM_HOST,
             'nginx.org/proxy-set-headers': 'Authorization: $http_x_mcp_authorization,X-MCP-Authorization:',
             'nginx.org/proxy-read-timeout': '90s', 'nginx.org/proxy-send-timeout': '90s',
             'nginx.org/client-max-body-size': '64k', 'nginx.org/proxy-buffering': 'false',
             'nginx.org/limit-req-rate': '30r/s', 'nginx.org/limit-req-key': '${binary_remote_addr}',
             'nginx.org/limit-req-burst': '90', 'nginx.org/limit-req-no-delay': 'true',
             'nginx.org/limit-req-reject-code': '429'}},
         'spec': {'ingressClassName': 'iot-gateway',
                  'tls': [{'hosts': [HOST], 'secretName': 'mcp-gateway-tls'}],
                  'rules': [{'host': HOST, 'http': {'paths': [
                      {'path': '/', 'pathType': 'Prefix', 'backend': {
                          'service': {'name': SERVICE, 'port': {'number': 8080}}}}]}}]}}
    ]
