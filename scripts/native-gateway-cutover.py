"""Verify a scoped, reversible Agentgateway cutover for the three retained production agents."""

import argparse
from datetime import datetime, timezone
from http.client import HTTPConnection
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


SMOKE = importlib.util.spec_from_file_location('native_gateway_smoke', Path(__file__).with_name('native-gateway-driver.py'))
smoke = importlib.util.module_from_spec(SMOKE)
sys.modules[SMOKE.name] = smoke
SMOKE.loader.exec_module(smoke)
from native_gateway_smoke import K, RUN_ANNOTATION, command, deadlines, read, ready_endpoints, save, secret, wait_forward


def identity(value):
    """Return an identity that survives spec changes and excludes volatile readback metadata."""
    return value['metadata']['uid'], value['spec']


def patch(resource, previous, next_spec, dry=False):
    """Require the observed UID, resourceVersion and full spec before replacing reviewed fields."""
    metadata = previous['metadata']
    ops = [{'op': 'test', 'path': '/metadata/' + name, 'value': metadata[name]}
           for name in ('uid', 'resourceVersion')]
    ops += [{'op': 'test', 'path': '/spec', 'value': previous['spec']},
            {'op': 'replace', 'path': '/spec', 'value': next_spec}]
    args = ['patch', resource, metadata['name'], '-n', metadata['namespace'], '--type=json', '-p', json.dumps(ops)]
    if dry:
        args.append('--dry-run=server')
    command(*args)


def labels_match(selector, labels):
    """Evaluate Kubernetes podSelector matching for the retained policies."""
    if any(labels.get(k) != v for k, v in selector.get('matchLabels', {}).items()):
        return False
    for item in selector.get('matchExpressions', []):
        key, operator = item['key'], item['operator']
        values = item.get('values', [])
        if operator == 'In' and labels.get(key) not in values:
            return False
        if operator == 'NotIn' and labels.get(key) in values:
            return False
        if operator == 'Exists' and key not in labels:
            return False
        if operator == 'DoesNotExist' and key in labels:
            return False
    return True


def egress_policy(namespace, name, run, match, gateway_labels, ports):
    """Add a bounded destination to an already isolated client selector."""
    return {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
            'metadata': {'name': name, 'namespace': namespace, 'annotations': {RUN_ANNOTATION: run}},
            'spec': {'podSelector': {'matchLabels': match}, 'policyTypes': ['Egress'],
                     'egress': [{'to': [{'podSelector': {'matchLabels': gateway_labels}}],
                                 'ports': [{'protocol': 'TCP', 'port': port} for port in ports]}]}}


def agents_ready(namespace, plan, timeout=170):
    """Wait for the exact planned production specs and the controller's current Ready/Accepted generations."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        actual = read('sandboxagents', '-n', namespace)['items']
        by_name = {v['metadata']['name']: v for v in actual}
        waiting = []
        for name, (uid, spec) in plan.items():
            value = by_name[name]
            if identity(value) != (uid, spec):
                raise ValueError('Agent identity/spec changed during reconciliation: ' + name)
            generation = value['metadata']['generation']
            status = value.get('status', {})
            conditions = {c['type'] for c in status.get('conditions', [])
                          if c['status'] == 'True' and c.get('observedGeneration') == generation}
            if status.get('observedGeneration') != generation or not {'Ready', 'Accepted'} <= conditions:
                waiting.append(name)
        if not waiting:
            return actual
        time.sleep(3)
    raise ValueError('The patched SandboxAgent generations did not become Ready and Accepted')


def log_records(raw, banned):
    """Parse native JSON lines only after scanning the credentials used for this run."""
    if any(item.encode() in raw for item in banned):
        raise ValueError('Private value in process logs. No log capture written')
    records = []
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def worker_ips(namespace, roles):
    """Map actual running worker Pod IPs to the retained roles."""
    result = {}
    for role in roles:
        items = read('pods', '-n', namespace, '-l', 'ate.dev/worker-pool=iot-' + role + '-pool')['items']
        for pod in items:
            address = pod.get('status', {}).get('podIP')
            if address and pod['status'].get('phase') == 'Running':
                result[address] = {'role': role, 'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid']}
    if {item['role'] for item in result.values()} != set(roles):
        raise ValueError('Need active worker Pods for every retained role')
    return result


def role_of(line, workers):
    """Attribute a real TCP source to the retained worker inventory."""
    address = line.get('src.addr', '').rsplit(':', 1)[0]
    return workers.get(address, {}).get('role')


def collect_route_evidence(gateway, observer, workers, roles):
    """Join the gateway's real response IDs to real model generations and require both MCP roles."""
    upstream = {v['request_id']: v for v in observer if v.get('event') == 'model_request'}
    correlated = []
    mcp = []
    for item in gateway:
        route = item.get('route', '').rsplit('/', 1)[-1]
        if item.get('http.status') != 200:
            continue
        role = role_of(item, workers)
        if route == 'model' and item.get('http.path') == '/v1/chat/completions':
            fact = upstream.get(item.get('observer_request_id'))
            # The observer's tool-set heuristic can emit unknown for valid ADK
            # turns. The exact originating worker IP provides the role here.
            if (fact and role in roles and fact['agent'] in {role, 'unknown'} and fact['model'] == 'iot-qwen2.5-7b'
                    and fact['status'] == 200 and fact['result'] == 'ok' and fact['usage']['completion_tokens'] > 0):
                correlated.append({'role': role, 'gateway': item, 'observer': fact})
        if (route in roles[:2] and role == route and item.get('mcp.method.name') == 'tools/call'
                and item.get('mcp.error.code') is None):
            mcp.append(item)
    expected = {'get_machine_features', 'predict_failure', 'search_knowledge', 'assess_feature_drift'}
    if {x['role'] for x in correlated} != set(roles) or not expected <= {x.get('gen_ai.tool.name') for x in mcp}:
        raise ValueError('Incomplete role-bound model or MCP execution logs')
    a2a = [x for x in gateway if x.get('route', '').endswith(('/a2a-coordinator', '/a2a-coordinator-slash'))
           and x.get('a2a.method') == 'message/send' and x.get('http.status') == 200]
    if len(a2a) != 1:
        raise ValueError('Expected one successful coordinator A2A call in the new capture window')
    return {'model': correlated, 'mcp': mcp, 'a2a_coordinator': a2a[0]}


def child_history(host, role, session_id):
    """Read the genuine child session and task from the same private controller identity."""
    if not re.fullmatch(r'[0-9a-f-]{36}', session_id):
        raise ValueError('Unexpected native session identity')
    result = {}
    for kind, suffix in [('session', ''), ('tasks', '/tasks')]:
        conn = HTTPConnection(host, 8083, timeout=20)
        try:
            conn.request('GET', '/api/sessions/' + session_id + suffix,
                         headers={'X-User-Id': 'coursework', 'Accept': 'application/json'})
            response = conn.getresponse()
            body = response.read(2 * 1024 * 1024 + 1)
            if response.status != 200 or len(body) > 2 * 1024 * 1024:
                raise ValueError('Private child session readback failed for ' + role)
            result[kind] = json.loads(body)
        finally:
            conn.close()
    return result


def run(args):
    """Exercise a temporary candidate connection and restore the exact prior specs by default."""
    sys.path.insert(0, str(args.source.resolve()))
    from infra.agentgateway.candidate import BACKENDS, DNS_NAME, NAME, NAMESPACE, PORTS, ROLES, SELECTOR, bearer_hash
    from infra.agentgateway.probe import GatewayProbe
    root, out, agent_source = args.runtime_root.resolve(), args.output.resolve(), args.agent_source.resolve()
    sys.path.insert(0, str(agent_source))
    from agents.ci_contract import validate_source
    from agents.delegation_acceptance import DELEGATES, verify_coordinator
    from agents.probe import QUESTIONS
    from agents.release_ci import feature_lease, ready_agents

    class BoundedProbe(GatewayProbe):
        """Finish the blocking conversation before the feature lease and VM rollback reserve."""

        def request(self, *arguments, timeout=45, **options):
            """Cap the candidate's normal large A2A transport timeout for this run."""
            return super().request(*arguments, timeout=min(timeout, 420), **options)

    if (os.geteuid() != 0 or not root.is_relative_to('/srv/iot-data') or not out.is_relative_to(root / 'outputs')
            or not re.fullmatch(r'agw-[a-z0-9]{1,24}', args.run)):
        raise ValueError('Require the retained control VM, fresh project output and short run name')
    if json.loads(command('config', 'view', '--minify', '-o', 'json'))['clusters'][0]['cluster']['server'] not in {'https://127.0.0.1:6443','https://0.0.0.0:6443'}:
        raise ValueError('Refusing a kubeconfig for another cluster')
    cutoffs = deadlines(root)
    seal = json.loads((agent_source.parent / 'manifest.json').read_bytes())
    source_digest = validate_source(agent_source, seal, seal['commit'])
    smoke_dir = args.smoke.resolve()
    if not smoke_dir.is_relative_to(root / 'outputs') or not json.loads((smoke_dir / 'verification.json').read_bytes())['passed']:
        raise ValueError('Require the successful retained native candidate smoke')
    candidate = read('service', NAME, '-n', NAMESPACE)
    original_candidate = json.loads((smoke_dir / 'candidate-service.json').read_bytes())
    if identity(candidate) != identity(original_candidate) or candidate['spec']['type'] != 'ClusterIP':
        raise ValueError('Candidate identity or service placement changed')
    ready_endpoints(NAMESPACE, NAME)
    tokens = {'model': secret(NAMESPACE, 'iot-local-model-client', 'API_KEY'),
              'retrieval': secret(NAMESPACE, 'iot-retrieval-mcp-auth', 'token'),
              'drift': secret(NAMESPACE, 'iot-drift-mcp-auth', 'token'),
              'a2a': (smoke_dir / '.a2a-bearer').read_text().strip()}
    candidate_config = read('configmap', NAME, '-n', NAMESPACE)['data']['config.yaml']
    if any(bearer_hash(token) not in candidate_config for token in tokens.values()) or 'observer_request_id' not in candidate_config:
        raise ValueError('Candidate lacks the expected scoped authentication and observer correlation')
    if tokens['model'] != secret('iot-llm', 'iot-llm-observer-client', 'API_KEY'):
        raise ValueError('Model credential binding changed after smoke')
    for role in ROLES[:2]:
        if secret(NAMESPACE, 'iot-agent-tool-headers', role) != 'Bearer ' + tokens[role]:
            raise ValueError('MCP credential binding changed after smoke')

    production = {'iot-' + role for role in ROLES}
    existing = read('sandboxagents', '-n', NAMESPACE)['items']
    baseline_agents = {a['metadata']['name']: a for a in existing}
    ready_agents([a for a in existing if a['metadata']['name'] in production],
                 json.loads((agent_source / 'agents/response-policy.json').read_bytes()))
    default_users = {a['metadata']['name'] for a in existing
                     if a['spec'].get('declarative', {}).get('modelConfig') == 'default-model-config'}
    if default_users != production:
        raise ValueError('The default ModelConfig must select exactly the three reviewed agents')
    controls = read('deployment', 'kagent-controller', '-n', NAMESPACE)['spec']['template']['metadata']['labels']
    policies = read('networkpolicies', '-n', NAMESPACE)['items']
    originals = {p['metadata']['name']: p for p in policies}
    additions, changes = [], []
    controller_selector = {'app.kubernetes.io/name': 'kagent', 'app.kubernetes.io/component': 'controller'}
    if not all(controls.get(key)==value for key,value in controller_selector.items()):
        raise ValueError('Controller labels differ from the gateway ingress selector')
    if any('Egress' in p['spec'].get('policyTypes', []) and labels_match(p['spec']['podSelector'], controls) for p in policies):
        controller_pods = read('pods', '-n', NAMESPACE, '-l', ','.join(key+'='+value for key,value in controller_selector.items()))['items']
        if len(controller_pods)!=1 or not controller_pods[0]['metadata']['name'].startswith('kagent-controller-'):
            raise ValueError('Controller egress policy must select only the existing controller Pod')
        additions.append(egress_policy(NAMESPACE, args.run+'-controller', args.run, controller_selector, SELECTOR,
                                       [PORTS['retrieval'],PORTS['drift']]))
    for role in ROLES:
        match = {'ate.dev/worker-pool': 'iot-' + role + '-pool'}
        old = originals['iot-' + role + '-worker-egress']['spec']
        if old['podSelector'] != {'matchLabels': match} or old['policyTypes'] != ['Egress']:
            raise ValueError('Existing worker egress isolation must match this exact role')
        additions.append(egress_policy(NAMESPACE, args.run + '-' + role, args.run, match, SELECTOR,
                                       [PORTS['model']] if role == 'coordinator' else [PORTS['model'], PORTS[role]]))
        previous = baseline_agents['iot-' + role]
        spec = json.loads(json.dumps(previous['spec']))
        domains = spec['sandbox']['network']['allowedDomains']
        if DNS_NAME in domains:
            raise ValueError('The live agent already includes the candidate hostname')
        domains.append(DNS_NAME)
        if role != 'coordinator':
            original_name = 'iot-' + role + '-tools'
            remote = read('remotemcpserver', original_name, '-n', NAMESPACE)
            remote_spec = json.loads(json.dumps(remote['spec']))
            if remote_spec['url'] != f'http://{BACKENDS[role]}/mcp' or remote_spec.get('headers'):
                raise ValueError('Expected original scoped MCP transport before cloning')
            new_name = args.run + '-' + role + '-tools'
            remote_spec['url'] = f'http://{DNS_NAME}:{PORTS[role]}/mcp'
            additions.append({'apiVersion': remote['apiVersion'], 'kind': remote['kind'],
                'metadata': {'name': new_name, 'namespace': NAMESPACE, 'annotations': {RUN_ANNOTATION: args.run}},
                'spec': remote_spec})
            tool = next(t['mcpServer'] for t in spec['declarative']['tools'] if t.get('type') == 'McpServer')
            if tool['name'] != original_name:
                raise ValueError('The live agent has a different MCP resource')
            tool['name'] = new_name
        changes.append(('sandboxagent', previous, spec))
    model = read('modelconfig', 'default-model-config', '-n', NAMESPACE)
    model_spec = json.loads(json.dumps(model['spec']))
    if model_spec['openAI']['baseUrl'] != f'http://{BACKENDS["model"]}/v1':
        raise ValueError('The shared model is already on another route')
    model_spec['openAI']['baseUrl'] = f'http://{DNS_NAME}:{PORTS["model"]}/v1'
    changes.append(('modelconfig', model, model_spec))

    feature = read('deployment', 'iot-feature-api', '-n', 'iot-features')
    seed_name = next(v['configMap']['name'] for v in feature['spec']['template']['spec']['volumes'] if v['name'] == 'seed')
    seed = read('configmap', seed_name, '-n', 'iot-features')
    context = json.loads(seed['data']['context.json'])
    lease = feature_lease(context, datetime.now(timezone.utc), minimum_seconds=800)
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    save(out / 'plan.json', {'source_commit': seal['commit'], 'source_digest': source_digest, 'stop_cutoffs': cutoffs,
         'lease': lease, 'candidate_uid': candidate['metadata']['uid'], 'additions': additions,
         'changes': [{'kind': kind, 'before': previous['spec'], 'after': spec, 'uid': previous['metadata']['uid']}
                     for kind, previous, spec in changes]})
    for addition in additions:
        if command('get', addition['kind'].lower(), addition['metadata']['name'], '-n', NAMESPACE,
                   '--ignore-not-found', '-o', 'name').strip():
            raise ValueError('Cutover resource already exists')
        command('create', '--dry-run=server', '-f', '-', payload=json.dumps(addition).encode())
    for kind, previous, spec in changes:
        patch(kind, previous, spec, dry=True)

    created = []
    accepted = False
    try:
        for addition in additions:
            value = json.loads(command('create', '-f', '-', '-o', 'json', payload=json.dumps(addition).encode()))
            created.append((value['kind'].lower(), value['metadata']['name'], value['metadata']['uid']))
        for kind, previous, spec in changes:
            live = read(kind, previous['metadata']['name'], '-n', NAMESPACE)
            if identity(live) != identity(previous):
                raise ValueError('Reviewed spec or UID changed before cutover')
            patch(kind, live, spec)
        planned = {v['metadata']['name']: (v['metadata']['uid'], spec) for kind, v, spec in changes if kind == 'sandboxagent'}
        current = agents_ready(NAMESPACE, planned)
        after_model = read('modelconfig', 'default-model-config', '-n', NAMESPACE)
        if identity(after_model) != (model['metadata']['uid'], model_spec):
            raise ValueError('Shared ModelConfig readback differs from the planned change')
        for item in current:
            name = item['metadata']['name']
            if name not in production and identity(item) != identity(baseline_agents[name]):
                raise ValueError('A nonproduction SandboxAgent changed during cutover')
        save(out / 'applied-agents.json', [{'name': v['metadata']['name'], 'uid': v['metadata']['uid'],
             'generation': v['metadata']['generation'], 'spec': v['spec']} for v in current])
        save(out / 'applied-model.json', after_model)
        workers = worker_ips(NAMESPACE, ROLES)
        feature_lease(context, datetime.now(timezone.utc), minimum_seconds=430)
        since = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
        save(out / 'capture-window.json', {'since_utc':since})
        role_ports = {role: args.loopback_start_port + i for i, role in enumerate(PORTS)}
        with (out / 'forward.log').open('wb') as log:
            with subprocess.Popen([*K, 'port-forward', '-n', NAMESPACE, 'service/' + NAME, '--address=127.0.0.1',
                                   *(f'{role_ports[role]}:{port}' for role, port in PORTS.items())],
                                  stdout=log, stderr=log) as forward:
                try:
                    wait_forward(forward, role_ports.values())
                    probe = BoundedProbe('127.0.0.1', tokens, out / 'gateway-probe', role_ports)
                    reply = probe.a2a('coordinator', QUESTIONS['coordinator'])
                finally:
                    if forward.poll() is None:
                        forward.terminate()
                        forward.wait(timeout=10)
        controller = read('service', 'kagent-controller', '-n', NAMESPACE)['spec']['clusterIP']
        children = {}
        for message in reply['result'].get('history', []):
            for part in message.get('parts', []):
                data = part.get('data', {})
                role = DELEGATES.get(data.get('name'))
                if role and part.get('metadata', {}).get('adk_type') == 'function_response':
                    children[role] = child_history(controller, role, data['response']['subagent_session_id'])
        save(out / 'child-histories.json', children)
        workers.update(worker_ips(NAMESPACE, ROLES))
        save(out / 'worker-identities.json', workers)
        banned = list(tokens.values())
        gateway_raw = command('logs', 'deployment/' + NAME, '-n', NAMESPACE, '--since-time=' + since, '--tail=1200')
        observer_raw = command('logs', 'deployment/iot-llm-observer', '-n', 'iot-llm', '-c', 'observer',
                               '--since-time=' + since, '--tail=1200')
        gateway = log_records(gateway_raw, banned)
        observer = log_records(observer_raw, banned)
        (out/'gateway-native.log').write_bytes(gateway_raw)
        (out/'observer-native.log').write_bytes(observer_raw)
        result = verify_coordinator(reply, children, knowledge_root=agent_source / 'knowledge')
        save(out / 'conversation-acceptance.json', result)
        evidence = collect_route_evidence(gateway, observer, workers, ROLES)
        save(out / 'route-evidence.json', evidence)
        save(out / 'verification.json', {'passed': True, 'all_three_model_roles_correlated': True,
             'both_mcp_roles_routed': True, 'native_child_acceptance': result,
             'parent_a2a_entry_via_agentgateway': True, 'child_a2a_entry_via_agentgateway': False,
             'kept_requested': args.keep})
        accepted = True
    finally:
        if not accepted or not args.keep:
            for kind, previous, spec in reversed(changes):
                current = read(kind, previous['metadata']['name'], '-n', NAMESPACE)
                if identity(current) == identity(previous):
                    continue
                if identity(current) != (previous['metadata']['uid'], spec):
                    raise ValueError('Concurrent spec/UID change prevents automatic rollback of ' + previous['metadata']['name'])
                patch(kind, current, previous['spec'])
            restored = {n: identity(v) for n, v in baseline_agents.items() if n in production}
            agents_ready(NAMESPACE, restored)
            for kind, name, uid in reversed(created):
                current = read(kind, name, '-n', NAMESPACE)
                if current['metadata']['uid'] != uid or current['metadata']['annotations'].get(RUN_ANNOTATION) != args.run:
                    raise ValueError('Owned resource identity changed before rollback: ' + name)
                command('delete', kind, name, '-n', NAMESPACE, '--wait=true')
            save(out / 'rollback.json', {'restored': True, 'production_agents': sorted(production), 'removed': created})


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'agent-source', 'runtime-root', 'smoke', 'output'):
        cli.add_argument('--' + name, required=True, type=Path)
    cli.add_argument('--run', required=True)
    cli.add_argument('--keep', action='store_true')
    cli.add_argument('--loopback-start-port', default=28180, type=int)
    args = cli.parse_args()
    if not 1024 <= args.loopback_start_port <= 65532:
        cli.error('Choose four consecutive nonprivileged loopback ports')
    run(args)
