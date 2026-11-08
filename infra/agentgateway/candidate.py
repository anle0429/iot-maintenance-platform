"""Render an isolated agentgateway candidate against the retained private services."""

from hashlib import sha256
import json
import re
from infra.llm_observer_routing import gateway_trace_config


NAME = 'iot-agentgateway-candidate'
NAMESPACE = 'iot-agents'
DNS_NAME = f'{NAME}.{NAMESPACE}.svc.cluster.local'
IMAGE = 'cr.agentgateway.dev/agentgateway@sha256:460dda874be2dfa95fa5713c1f46f26079dce78df576008df704e54a59d5780d'
PORTS = {'model': 18080, 'retrieval': 18081, 'drift': 18082, 'a2a': 18083}
BACKENDS = {
    'model': 'iot-router-epp.iot-llm.svc.cluster.local:8081',
    'retrieval': 'iot-retrieval-mcp.iot-agents.svc.cluster.local:8080',
    'drift': 'iot-drift-mcp.iot-agents.svc.cluster.local:8080',
    'a2a': 'kagent-controller.iot-agents.svc.cluster.local:8083',
}
ROLES = ('retrieval', 'drift', 'coordinator')
SELECTOR = {'app.kubernetes.io/instance': NAME}


def a2a_path(role):
    """Limit A2A to the three existing SandboxAgent paths."""
    if role not in ROLES:
        raise ValueError('Unknown agent role')
    return f'/api/a2a-sandboxes/iot-agents/iot-{role}'


def bearer_hash(token):
    """Hash a scoped credential without serializing its plaintext to configuration."""
    if not re.fullmatch(r'[A-Za-z0-9_.~+/=-]{32,256}', token):
        raise ValueError('Expected one bounded scoped bearer token')
    return 'sha256:' + sha256(token.encode()).hexdigest()


def config(hashes, *, backends=None, ports=None, credential_dir='/credentials', local=False,tracing=False,
           delegation_hash=None):
    """Build the standalone v1.7.0 configuration with role-specific credentials and exact routes."""
    if set(hashes) != set(PORTS) or len(set(hashes.values())) != len(PORTS):
        raise ValueError('Require four different scoped client credentials')
    if any(not re.fullmatch(r'sha256:[0-9a-f]{64}', digest) for digest in hashes.values()):
        raise ValueError('Only SHA-256 credential hashes are accepted')
    if delegation_hash is not None and (not re.fullmatch(r'sha256:[0-9a-f]{64}', delegation_hash)
                                        or delegation_hash in hashes.values()):
        raise ValueError('Delegation needs its own distinct SHA-256 credential hash')
    ports = ports or PORTS
    backends = backends or BACKENDS

    def route(name, role, paths, backend, timeout):
        """Attach exact path/method matching, authentication and a response route marker."""
        policy = {
            'apiKey': {'mode': 'strict', 'keys': [{'keyHash': hashes[role]}]},
            'responseHeaderModifier': {'set': {'x-iot-agentgateway-route': name}},
            'timeout': {'requestTimeout': timeout},
        }
        if local:
            policy['authorization'] = {'rules': ['cidr("127.0.0.1/32").containsIP(source.address)']}
        return {'name': name, 'matches': [{'method': method, 'path': {'exact': path}}
                                          for method, path in paths],
                'backends': [backend], 'policies': policy}

    model = route('model', 'model', [('GET', '/v1/models'), ('POST', '/v1/chat/completions')],
                  {'host': backends['model'], 'policies': {'backendAuth': {
                      'key': {'file': f'{credential_dir}/model'}}}}, '610s')
    # API-key authentication consumes the caller header. The model observer
    # independently validates the scoped backend bearer attached by backendAuth.
    routes = {'model': [model]}
    for role in ROLES[:2]:
        upstream = {'name': role, 'mcp': {'host': 'http://' + backends[role] + '/mcp'},
                    'policies': {'backendAuth': {'key': {'file': f'{credential_dir}/{role}'}}}}
        routes[role] = [route(role, role, [(method, '/mcp') for method in ('GET', 'POST', 'DELETE')],
                              {'mcp': {'targets': [upstream], 'prefixMode': 'never'}}, '60s')]
    routes['a2a'] = []
    for role in ROLES:
        path = a2a_path(role)
        current = route('a2a-' + role, 'a2a', [('GET', path + '/.well-known/agent-card.json'), ('POST', path)],
                        {'host': backends['a2a']}, '1800s')
        current['policies'].update({
            'a2a': {},
            'requestHeaderModifier': {'remove': ['authorization', 'x-forwarded-user'],
                                      'set': {'x-user-id': 'coursework'}},
        })
        routes['a2a'].append(current)
        # The controller advertises the slash form in the JSON-RPC interface.
        # Keep forwarding the exact controller endpoint used by existing callers.
        advertised = route('a2a-' + role + '-slash', 'a2a', [('POST', path + '/')],
                           {'host': backends['a2a']}, '1800s')
        advertised['policies'].update({**current['policies'], 'urlRewrite': {'path': {'full': path}}})
        routes['a2a'].append(advertised)
    if delegation_hash is not None:
        delegated = []
        for role in ROLES[:2]:
            path = a2a_path(role)
            methods = [('GET', path + '/.well-known/agent-card.json'), ('POST', path)]
            for suffix, matches in [('', methods), ('-slash', [('POST', path + '/')])]:
                current = route('delegate-' + role + suffix, 'a2a', matches,
                                {'host': backends['a2a']}, '1800s')
                for match in current['matches']:
                    match['headers'] = [{'name': 'x-kagent-host',
                                         'value': {'exact': 'kagent-controller.iot-agents'}}]
                current['policies'].update({
                    'apiKey': {'mode': 'strict', 'keys': [{'keyHash': delegation_hash}]},
                    'responseHeaderModifier': {'set': {'x-iot-agentgateway-route': 'delegate-' + role}},
                    'a2a': {},
                    # The trusted SDK supplies dynamic user/parent/root/trace headers.
                    # Only operator ingress below imposes the fixed operator identity.
                    'requestHeaderModifier': {'remove': ['authorization', 'x-forwarded-user', 'x-kagent-host']},
                })
                if suffix:
                    current['policies']['urlRewrite'] = {'path': {'full': path}}
                delegated.append(current)
        aliases = []
        for role in ROLES[:2]:
            aliases.append(route('proxy-mcp-' + role, role,
                [(method, '/mcp/' + role) for method in ('GET', 'POST', 'DELETE')],
                routes[role][0]['backends'][0], '60s'))
        # kagent's compiler proxy preserves paths but drops the original port.
        # Exact aliases keep the two MCP capabilities separate on one listener.
        # A compiler host match separates delegated A2A from operator ingress.
        routes['a2a'] = delegated + routes['a2a'] + aliases
    result={
        'config': {'logging': {'format': 'json', 'level': 'warn,request=info'}, 'adminAddr': '127.0.0.1:15000',
                   'readinessAddr': '0.0.0.0:19001', 'statsAddr': '127.0.0.1:19002'},
        'frontendPolicies': {'accessLog': {'add': {'observer_request_id':
            '"x-iot-request-id" in response.headers && '
            'response.headers["x-iot-request-id"].matches("^[0-9a-f]{32}$") '
            '? response.headers["x-iot-request-id"] : ""'}}},
        'binds': [{'port': ports[role], 'listeners': [{'protocol': 'HTTP', 'routes': routes[role]}]}
                  for role in PORTS],
    }
    if delegation_hash is not None:
        fields = result['frontendPolicies']['accessLog']['add']
        identifier = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        for label in ('parent', 'root'):
            header = 'x-kagent-' + label + '-context-id'
            fields['a2a_' + label + '_context_id'] = (
                f'"{header}" in request.headers && request.headers["{header}"].matches("{identifier}") '
                f'? request.headers["{header}"] : ""')
        fields['a2a_source'] = ('"x-kagent-source" in request.headers && '
            'request.headers["x-kagent-source"] == "agent" ? "agent" : ""')
    return gateway_trace_config(result,'iot_agentgateway') if tracing else result


def kube(kind, version, name, spec, namespace=NAMESPACE):
    """Attach consistent names and labels so only the candidate can be removed."""
    return {'apiVersion': version, 'kind': kind,
            'metadata': {'name': name, 'namespace': namespace, 'labels': SELECTOR}, 'spec': spec}


def peer(namespace, labels):
    """Require both the namespace and the intended workload labels for policy peers."""
    return {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': namespace}},
            'podSelector': {'matchLabels': labels}}


def tcp(port):
    """Describe one bounded TCP port for NetworkPolicy."""
    return [{'protocol': 'TCP', 'port': port}]


def manifests(hashes, run_name, *, delegation_hash=None):
    """Create only the new ClusterIP, one candidate Pod and narrow ingress/egress allowances."""
    if not re.fullmatch(r'agw-[a-z0-9-]{1,35}', run_name):
        raise ValueError('Use an agw- run name')
    model_peer = peer('iot-llm', {'llm-d-router-gateway': 'iot-router-epp'})
    controller_peer = peer(NAMESPACE, {'app.kubernetes.io/name': 'kagent',
                                      'app.kubernetes.io/component': 'controller'})
    worker_peers = {role: peer(NAMESPACE, {'ate.dev/worker-pool': 'iot-' + role + '-pool'})
                    for role in ROLES}
    port_specs = [{'name': role, 'port': port, 'targetPort': port} for role, port in PORTS.items()]
    credentials = [{'secret': {'name': 'iot-local-model-client',
                               'items': [{'key': 'API_KEY', 'path': 'model'}]}}]
    credentials.extend({'secret': {'name': 'iot-' + role + '-mcp-auth',
                                   'items': [{'key': 'token', 'path': role}]}} for role in ROLES[:2])
    cfg = kube('ConfigMap', 'v1', NAME, {})
    del cfg['spec']
    cfg['data'] = {'config.yaml': json.dumps(config(hashes,tracing=True,delegation_hash=delegation_hash), indent=2)}
    deployment = kube('Deployment', 'apps/v1', NAME, {
        'replicas': 1, 'selector': {'matchLabels': SELECTOR}, 'strategy': {'type': 'Recreate'},
        'template': {'metadata': {'labels': SELECTOR}, 'spec': {
            'nodeSelector': {'kubernetes.io/arch': 'amd64', 'node-role.kubernetes.io/control-plane': 'true'},
            'automountServiceAccountToken': False, 'enableServiceLinks': False,
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001,
                                'fsGroup': 10001, 'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [{
                'name': 'agentgateway', 'image': IMAGE, 'args': ['-f', '/route/config.yaml'],
                'ports': [{'name': item['name'], 'containerPort': item['port']} for item in port_specs],
                'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'},
                              'limits': {'cpu': '500m', 'memory': '384Mi'}},
                'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                    'capabilities': {'drop': ['ALL']}},
                'readinessProbe': {'httpGet': {'path': '/healthz/ready', 'port': 19001},
                                   'periodSeconds': 3, 'timeoutSeconds': 2},
                'volumeMounts': [{'name': 'routes', 'mountPath': '/route', 'readOnly': True},
                                 {'name': 'backend-credentials', 'mountPath': '/credentials', 'readOnly': True}],
            }],
            'volumes': [{'name': 'routes', 'configMap': {'name': NAME}},
                        {'name': 'backend-credentials', 'projected': {'defaultMode': 288, 'sources': credentials}}],
        }},
    })
    probe_peer = peer(NAMESPACE, {'iot.coursework/agentgateway-probe': run_name})
    rules = {'model': [*worker_peers.values(), controller_peer, probe_peer], 'a2a': [probe_peer]}
    if delegation_hash is not None:
        rules['a2a'] = [*worker_peers.values(), controller_peer, probe_peer]
    for role in ROLES[:2]:
        rules[role] = [worker_peers[role], controller_peer, probe_peer]
    egress = [{'to': [model_peer], 'ports': tcp(8081)},
              {'to': [controller_peer], 'ports': tcp(8083)},
              {'to': [peer(NAMESPACE, {'app.kubernetes.io/instance': 'iot-' + role + '-mcp'})
                      for role in ROLES[:2]], 'ports': tcp(8080)},
              {'to': [peer('kube-system', {'k8s-app': 'kube-dns'})],
               'ports': [{'protocol': protocol, 'port': 53} for protocol in ('TCP', 'UDP')]}]
    policy = kube('NetworkPolicy', 'networking.k8s.io/v1', NAME, {
        'podSelector': {'matchLabels': SELECTOR}, 'policyTypes': ['Ingress', 'Egress'],
        'ingress': [{'from': rules[role], 'ports': tcp(port)} for role, port in PORTS.items()], 'egress': egress,
    })
    service = kube('Service', 'v1', NAME, {'type': 'ClusterIP', 'selector': SELECTOR, 'ports': port_specs})
    # Do not select any existing Pod with an egress policy here. Applying its
    # first egress selector would isolate a previously unrestricted workload.
    # Client policy changes require native before/after inspection.
    return [cfg, policy, service, deployment]
