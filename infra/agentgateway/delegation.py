"""Build reviewable, guarded delegation rollout plans without performing live actions."""
from copy import deepcopy
from hashlib import sha256
import json
import re

from infra.agentgateway.candidate import IMAGE, NAME, NAMESPACE, PORTS, ROLES, SELECTOR, config, peer, tcp


RUN_ANNOTATION = 'iot.coursework/agentgateway-delegation-run'
CONTROLLER_SELECTOR = {'app.kubernetes.io/name': 'kagent', 'app.kubernetes.io/component': 'controller'}
VERSIONS = {'ConfigMap': 'v1', 'Deployment': 'apps/v1', 'NetworkPolicy': 'networking.k8s.io/v1', 'Service': 'v1'}


def _digest(value):
    """Hash a complete reviewed field using stable JSON serialization."""
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _identity(resource):
    """Require a concrete named, namespaced and versioned Kubernetes readback."""
    if not isinstance(resource, dict) or resource.get('kind') not in VERSIONS:
        raise ValueError('Unexpected resource kind in readback')
    kind = resource['kind']
    metadata = resource.get('metadata', {})
    if (resource.get('apiVersion') != VERSIONS[kind] or metadata.get('namespace') != NAMESPACE
            or any(not isinstance(metadata.get(key), str) or not metadata[key]
                   for key in ('name', 'uid', 'resourceVersion'))):
        raise ValueError('Require exact namespace, UID and resourceVersion readbacks')
    return {'kind': kind, 'name': metadata['name'], 'namespace': NAMESPACE,
            'uid': metadata['uid'], 'resource_version': metadata['resourceVersion']}


def _labels_match(selector, labels):
    """Evaluate supported Kubernetes selectors and reject malformed or unknown operators."""
    if not isinstance(selector, dict) or set(selector) - {'matchLabels', 'matchExpressions'}:
        raise ValueError('Unrecognized pod selector')
    match_labels, expressions = selector.get('matchLabels', {}), selector.get('matchExpressions', [])
    if not isinstance(match_labels, dict) or not isinstance(expressions, list):
        raise ValueError('Malformed pod selector')
    matched = all(labels.get(key) == value for key, value in match_labels.items())
    for expression in expressions:
        if not isinstance(expression, dict) or set(expression) - {'key', 'operator', 'values'}:
            raise ValueError('Malformed pod selector expression')
        key, operator, values = expression.get('key'), expression.get('operator'), expression.get('values', [])
        if not isinstance(key, str) or not key or not isinstance(values, list):
            raise ValueError('Malformed pod selector expression')
        if operator in {'In', 'NotIn'}:
            if not values or any(not isinstance(value, str) for value in values):
                raise ValueError('Selector membership requires concrete values')
            selected = labels.get(key) in values
            if operator == 'NotIn': selected = not selected
        elif operator in {'Exists', 'DoesNotExist'}:
            if values: raise ValueError('Existence selectors cannot specify values')
            selected = key in labels
            if operator == 'DoesNotExist': selected = not selected
        else:
            raise ValueError('Unknown pod selector operator')
        matched = matched and selected
    return matched


def _guarded_patch(resource, field, replacement):
    """Bind one field replacement to its complete original value and Kubernetes identity."""
    identity = _identity(resource)
    previous = deepcopy(resource[field])
    return {**identity, 'field': field, 'before_sha256': _digest(previous), 'after_sha256': _digest(replacement),
            'operations': [
                {'op': 'test', 'path': '/metadata/uid', 'value': identity['uid']},
                {'op': 'test', 'path': '/metadata/resourceVersion', 'value': identity['resource_version']},
                {'op': 'test', 'path': '/' + field, 'value': previous},
                {'op': 'replace', 'path': '/' + field, 'value': deepcopy(replacement)},
            ]}


def _validate_private_gateway(gateway):
    """Preserve the pinned single gateway deployment and its private four-listener Service."""
    deployment, service = gateway['Deployment']['spec'], gateway['Service']['spec']
    pod = deployment['template']
    containers = pod['spec'].get('containers', [])
    expected_ports = [(role, port, port, 'TCP') for role, port in PORTS.items()]
    service_ports = [(value.get('name'), value.get('port'), value.get('targetPort'), value.get('protocol', 'TCP'))
                     for value in service.get('ports', [])]
    if (deployment.get('selector') != {'matchLabels': SELECTOR} or deployment.get('replicas') != 1
            or any(pod['metadata'].get('labels', {}).get(key) != value for key, value in SELECTOR.items())
            or len(containers) != 1 or containers[0].get('name') != 'agentgateway'
            or containers[0].get('image') != IMAGE or containers[0].get('args') != ['-f', '/route/config.yaml']
            or pod['spec'].get('hostNetwork', False)
            or pod['spec'].get('automountServiceAccountToken') is not False):
        raise ValueError('Preserve the pinned private gateway Deployment contract')
    container_ports = [(value.get('name'), value.get('containerPort'), value.get('protocol', 'TCP'))
                       for value in containers[0].get('ports', [])]
    if (container_ports != [(role, port, 'TCP') for role, port in PORTS.items()]
            or any(value.get('hostPort') for value in containers[0].get('ports', []))
            or service.get('type') != 'ClusterIP' or service.get('selector') != SELECTOR
            or service_ports != expected_ports or any(value.get('nodePort') for value in service.get('ports', []))
            or service.get('externalIPs') or service.get('externalName') or service.get('loadBalancerIP')
            or service.get('clusterIP') == 'None'):
        raise ValueError('Preserve the private four-port Service and container listeners')


def _gateway_ingress(resource, run):
    """Add only exact run-probe peers and the clients required on existing TCP18083 ingress."""
    spec = deepcopy(resource['spec'])
    ingress = spec.get('ingress', [])
    if (spec.get('podSelector') != {'matchLabels': SELECTOR}
            or set(spec.get('policyTypes', [])) != {'Ingress', 'Egress'} or len(ingress) != len(PORTS)):
        raise ValueError('Require the retained gateway ingress/egress policy')
    seen = set()
    for rule in ingress:
        ports = rule.get('ports', [])
        if (len(ports) != 1 or ports[0].get('protocol', 'TCP') != 'TCP'
                or ports[0].get('port') not in PORTS.values() or ports[0].get('endPort')
                or not isinstance(rule.get('from'), list) or not rule['from']):
            raise ValueError('Require one existing exact listener port per ingress rule')
        port = ports[0]['port']
        if port in seen: raise ValueError('Duplicate gateway listener ingress')
        seen.add(port)
        additions = [peer(NAMESPACE, {'iot.coursework/agentgateway-probe': run})]
        if port == PORTS['a2a']:
            additions += [peer(NAMESPACE, {'ate.dev/worker-pool': 'iot-' + role + '-pool'}) for role in ROLES]
            additions.append(peer(NAMESPACE, CONTROLLER_SELECTOR))
        for addition in additions:
            if addition not in rule['from']: rule['from'].append(addition)
    return spec


def _egress_policy(name, run, selector):
    """Allow only gateway TCP18083 for a client that is already egress-isolated."""
    return {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
            'metadata': {'name': name, 'namespace': NAMESPACE, 'annotations': {RUN_ANNOTATION: run}},
            'spec': {'podSelector': {'matchLabels': deepcopy(selector)}, 'policyTypes': ['Egress'],
                     'egress': [{'to': [peer(NAMESPACE, SELECTOR)], 'ports': tcp(PORTS['a2a'])}]}}


def plan(existing_gateway_objects, network_policies, controller_labels, hashes, delegation_hash, run):
    """Return guarded field patches and bounded policy additions; never read or change a cluster."""
    try:
        return _plan(existing_gateway_objects, network_policies, controller_labels, hashes, delegation_hash, run)
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError('Malformed or incomplete delegation readback') from error


def _plan(existing_gateway_objects, network_policies, controller_labels, hashes, delegation_hash, run):
    """Validate the complete observed contracts before constructing an independent plan."""
    if not isinstance(run, str) or not re.fullmatch(r'agw-[a-z0-9-]{1,35}', run):
        raise ValueError('Use a bounded agw- run name')
    if (not isinstance(controller_labels, dict)
            or any(controller_labels.get(key) != value for key, value in CONTROLLER_SELECTOR.items())):
        raise ValueError('Require the retained controller labels')
    if delegation_hash is None: raise ValueError('Require the distinct delegation credential hash')
    old_config, new_config = config(hashes, tracing=True), config(hashes, tracing=True, delegation_hash=delegation_hash)
    if not isinstance(existing_gateway_objects, list) or len(existing_gateway_objects) != len(VERSIONS):
        raise ValueError('Require exactly the four retained gateway objects')
    gateway = {}
    for resource in existing_gateway_objects:
        identity = _identity(resource)
        if (identity['name'] != NAME or identity['kind'] in gateway
                or any(resource['metadata'].get('labels', {}).get(key) != value for key, value in SELECTOR.items())):
            raise ValueError('Require each owned gateway identity exactly once')
        gateway[identity['kind']] = resource
    if set(gateway) != set(VERSIONS): raise ValueError('Missing retained gateway resource kind')
    current_data = gateway['ConfigMap']['data']
    if (set(current_data) != {'config.yaml'} or gateway['ConfigMap'].get('binaryData')
            or json.loads(current_data['config.yaml']) not in (old_config, new_config)):
        raise ValueError('Gateway configuration differs from both exact reviewed source configurations')
    _validate_private_gateway(gateway)

    if not isinstance(network_policies, list): raise ValueError('Require observed namespace network policies')
    policies, policy_inventory = {}, []
    for resource in network_policies:
        identity = _identity(resource)
        if identity['kind'] != 'NetworkPolicy' or identity['name'] in policies:
            raise ValueError('Require unique NetworkPolicy readbacks')
        _labels_match(resource['spec']['podSelector'], controller_labels)
        policies[identity['name']] = resource
        policy_inventory.append({**identity, 'sha256': _digest(resource['spec'])})
    additions = []
    for role in ROLES:
        name = 'iot-' + role + '-worker-egress'
        selector = {'ate.dev/worker-pool': 'iot-' + role + '-pool'}
        if (name not in policies or policies[name]['spec'].get('podSelector') != {'matchLabels': selector}
                or policies[name]['spec'].get('policyTypes') != ['Egress']):
            raise ValueError('Require pre-existing exact worker egress isolation for ' + role)
        additions.append(_egress_policy(run + '-' + role, run, selector))
    controller_isolated = any('Egress' in resource['spec'].get('policyTypes', [])
                              and _labels_match(resource['spec']['podSelector'], controller_labels)
                              for resource in network_policies)
    if controller_isolated:
        additions.append(_egress_policy(run + '-controller', run, CONTROLLER_SELECTOR))
    if any(resource['metadata']['name'] in policies for resource in additions):
        raise ValueError('Run-scoped addition names already exist; use fresh observed ownership')

    new_data = {'config.yaml': json.dumps(new_config, indent=2)}
    deployment_spec = deepcopy(gateway['Deployment']['spec'])
    deployment_spec['template']['metadata'].setdefault('annotations', {})[RUN_ANNOTATION] = run
    ingress_spec = _gateway_ingress(gateway['NetworkPolicy'], run)
    patches = [_guarded_patch(gateway['ConfigMap'], 'data', new_data),
               _guarded_patch(gateway['Deployment'], 'spec', deployment_spec),
               _guarded_patch(gateway['NetworkPolicy'], 'spec', ingress_spec)]
    service = gateway['Service']
    return {'schema_version': 1, 'run': run, 'patches': patches, 'additions': additions,
            'unchanged': [{**_identity(service), 'sha256': _digest(service['spec'])}],
            'inventory': {'gateway': [_identity(resource) for resource in existing_gateway_objects],
                          'network_policies': policy_inventory, 'listener_ports': list(PORTS.values()),
                          'controller_already_egress_isolated': controller_isolated,
                          'new_policy_names': [resource['metadata']['name'] for resource in additions]}}
