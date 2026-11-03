"""Start only a new private Agentgateway candidate and probe its real backends."""

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4


K = ['/usr/local/bin/k3s', 'kubectl', '--kubeconfig=/etc/rancher/k3s/k3s.yaml']
RUN_ANNOTATION = 'iot.coursework/agentgateway-run'


def save(path, value):
    """Write one structured receipt with no secret values."""
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def command(*args, payload=None, timeout=25):
    """Bound the local cluster operation and keep its unfiltered streams private."""
    call = subprocess.run([*K, *args], input=payload, capture_output=True, timeout=timeout)
    if call.returncode:
        raise RuntimeError(f'Local kubectl {args[0]} failed with exit {call.returncode}')
    return call.stdout


def read(*args):
    """Get one actual cluster readback through the local kubeconfig."""
    return json.loads(command('get', *args, '-o', 'json'))


def secret(namespace, name, key):
    """Read a credential into memory only from its known Secret/key."""
    data = read('secret', name, '-n', namespace)['data']
    return base64.b64decode(data[key], validate=True).decode().strip()


def deadlines(root):
    """Require runway within the existing CPU and GPU stop windows."""
    state = root / 'data/runtime/ci'
    cpu = json.loads((state / 'session.json').read_bytes())
    gpu = json.loads((state / 'gpu-session.json').read_bytes())
    if gpu['status'] != 'RUNNING' or gpu['termination_action'] != 'STOP' or gpu['max_duration_seconds'] != 28800:
        raise ValueError('Require the approved running GPU session with its eight-hour stop limit')
    cutoffs = [datetime.fromisoformat(cpu['auto_stop_utc']),
               datetime.fromisoformat(gpu['last_start_utc']) + timedelta(seconds=gpu['max_duration_seconds'])]
    if min(cutoffs) - datetime.now(timezone.utc) < timedelta(minutes=15):
        raise ValueError('Require at least 15 minutes within both retained sessions')
    return [x.isoformat() for x in cutoffs]


def ready_endpoints(namespace, service):
    """Require at least one native Ready endpoint for the named private Service."""
    value = read('endpointslices', '-n', namespace, '-l', 'kubernetes.io/service-name=' + service)
    endpoints = [endpoint for item in value['items'] for endpoint in item['endpoints']
                 if endpoint.get('conditions', {}).get('ready') is True]
    if not endpoints:
        raise ValueError('No Ready endpoint for ' + service)
    return endpoints


def run(args):
    """Create reviewed resources and capture native requests via an owned loopback forward."""
    sys.path.insert(0, str(args.source.resolve()))
    from infra.agentgateway.candidate import NAME, NAMESPACE, PORTS, ROLES, bearer_hash, manifests
    from infra.agentgateway.probe import GatewayProbe, checked_tool_facts

    root = args.runtime_root.resolve()
    out = args.output.resolve()
    if not out.is_relative_to(root / 'outputs') or out.exists():
        raise ValueError('Choose a fresh output directory inside the retained runtime outputs')
    if os.geteuid() != 0 or not root.is_relative_to('/srv/iot-data'):
        raise ValueError('Use the retained Linux control VM and its local Kubernetes administrator')
    cluster = json.loads(command('config', 'view', '--minify', '-o', 'json'))
    if cluster['clusters'][0]['cluster']['server'] not in {'https://127.0.0.1:6443','https://0.0.0.0:6443'}:
        raise ValueError('Refusing a kubeconfig for another cluster')
    prior = args.resume.resolve() if args.resume else None
    if prior and (not prior.is_relative_to(root/'outputs') or json.loads((prior/'preflight.json').read_bytes())['run'] != args.run):
        raise ValueError('Resume requires the original candidate run receipt')
    tokens = {'model': secret(NAMESPACE, 'iot-local-model-client', 'API_KEY'),
              'retrieval': secret(NAMESPACE, 'iot-retrieval-mcp-auth', 'token'),
              'drift': secret(NAMESPACE, 'iot-drift-mcp-auth', 'token'),
              'a2a': (prior/'.a2a-bearer').read_text().strip() if prior else secrets.token_urlsafe(36)}
    observer_token = secret('iot-llm', 'iot-llm-observer-client', 'API_KEY')
    if not secrets.compare_digest(tokens['model'], observer_token):
        raise ValueError('Agent-side and observer model credentials differ')
    for role in ROLES[:2]:
        remote = secret(NAMESPACE, 'iot-agent-tool-headers', role)
        if not secrets.compare_digest(remote, 'Bearer ' + tokens[role]):
            raise ValueError('MCP client and backend credential references differ for ' + role)
    resources = manifests({role: bearer_hash(value) for role, value in tokens.items()}, args.run)
    kinds = [obj['kind'].lower() for obj in resources]
    for kind in kinds:
        if prior:
            actual = read(kind, NAME, '-n', NAMESPACE)
            if actual['metadata']['annotations'].get(RUN_ANNOTATION) != args.run:
                raise ValueError('Existing resource belongs to another run')
            continue
        if command('get', kind, NAME, '-n', NAMESPACE, '--ignore-not-found', '-o', 'name').strip():
            raise ValueError('Existing candidate resource found. Skipping all creation: ' + kind)
    node = read('nodes', '-l', 'kubernetes.io/arch=amd64,node-role.kubernetes.io/control-plane=true')
    if [n['metadata']['name'] for n in node['items']] != [args.cpu_node]:
        raise ValueError('The candidate node selector must select exactly the retained CPU node')
    cutoffs = deadlines(root)
    for namespace, service in [('iot-llm', 'iot-router-epp'), (NAMESPACE, 'iot-retrieval-mcp'),
                               (NAMESPACE, 'iot-drift-mcp'), (NAMESPACE, 'kagent-controller')]:
        ready_endpoints(namespace, service)
    deployment = read('deployment', 'iot-feature-api', '-n', 'iot-features')
    volumes = deployment['spec']['template']['spec']['volumes']
    seed_name = next(v['configMap']['name'] for v in volumes if v['name'] == 'seed')
    seed = read('configmap', seed_name, '-n', 'iot-features')
    context = json.loads(seed['data']['context.json'])
    expires = datetime.fromisoformat(context['prepared_wall_utc']) + timedelta(hours=1)
    if expires - datetime.now(timezone.utc) < timedelta(minutes=8):
        raise ValueError('Refresh the original Feature API lease before native MCP calls')

    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    save(out / 'preflight.json', {'run': args.run, 'checked_utc': datetime.now(timezone.utc).isoformat(),
         'cpu_node': args.cpu_node, 'cpu_and_gpu_stop_utc': cutoffs, 'all_candidate_names_absent': prior is None,
         'model_secret_bytes_match': True, 'mcp_secret_bytes_match': True,
         'feature_seed_uid': seed['metadata']['uid'], 'feature_lease_expires_utc': expires.isoformat()})
    private = os.open(out / '.a2a-bearer', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(private, 'w') as handle:
        handle.write(tokens['a2a'] + '\n')
    for obj in resources:
        obj['metadata']['annotations'] = {RUN_ANNOTATION: args.run}
    payload = json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': resources}).encode()
    if not prior:
        command('create', '--dry-run=server', '-f', '-', payload=payload, timeout=40)
        command('create', '-f', '-', payload=payload, timeout=45)
    command('rollout', 'status', 'deployment/' + NAME, '-n', NAMESPACE, '--timeout=180s', timeout=200)
    service = read('service', NAME, '-n', NAMESPACE)
    if (service['spec']['type'] != 'ClusterIP' or service['spec'].get('externalIPs')
            or service['metadata']['annotations'].get(RUN_ANNOTATION) != args.run):
        raise ValueError('Unexpected candidate Service placement or ownership')
    endpoints = ready_endpoints(NAMESPACE, NAME)
    save(out / 'candidate-service.json', service)
    save(out / 'candidate-endpoints.json', endpoints)
    ports = {role: args.loopback_start_port + i for i, role in enumerate(PORTS)}
    with (out / 'forward.log').open('wb') as log:
        with subprocess.Popen([*K, 'port-forward', '-n', NAMESPACE, 'service/' + NAME, '--address=127.0.0.1',
                               *(f'{ports[role]}:{port}' for role, port in PORTS.items())],
                              stdout=log, stderr=log) as process:
            try:
                wait_forward(process, ports.values())
                probe = GatewayProbe('127.0.0.1', tokens, out / 'probe', ports)
                if args.cards_only:
                    from hashlib import sha256
                    receipts = json.loads((prior/'probe/requests.json').read_bytes())
                    for key,role in [('model-completion','model'),('retrieval-tool','retrieval'),('drift-tool','drift')]:
                        actual = receipts[key]
                        assert actual['http_status']==200 and actual['gateway_route']==role
                        assert sha256((prior/'probe'/(key+'.body')).read_bytes()).hexdigest()==actual['body_sha256']
                else:
                    probe.model()
                    machine = checked_tool_facts('retrieval', probe.mcp('retrieval', {'machine_id': 'P-001'}))
                    window = 'agw-' + uuid4().hex[:12]
                    drift = checked_tool_facts('drift', probe.mcp('drift', {'window_id': window,
                        'machine_ids': [f'P-{i:03d}' for i in range(1, 33)]}), window=window)
                    if any(drift[key] != machine[key] for key in ('source_manifest_sha256', 'as_of')):
                        raise ValueError('MCP checks span different source snapshots')
                for role in ROLES:
                    probe.a2a(role)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
    save(out / 'verification.json', {'passed': True, 'requests': len(probe.receipts),
         'candidate_run': args.run, 'model_and_both_mcp_business_results': True,
         'retained_model_and_mcp_run': prior.name if args.cards_only else None,
         'a2a_cards': list(ROLES), 'shared_model_config_cutover': False, 'native_agent_delegation_checked': False})


def wait_forward(process, ports):
    """Wait only for the owned kubectl forward and its four loopback bindings."""
    end = time.monotonic() + 15
    while process.poll() is None and time.monotonic() < end:
        try:
            for port in ports:
                with socket.create_connection(('127.0.0.1', port), timeout=1):
                    pass
            return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError('Private kubectl forward did not bind all requested loopback ports')


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'runtime-root', 'output'):
        cli.add_argument('--' + name, required=True, type=Path)
    cli.add_argument('--run', required=True)
    cli.add_argument('--resume',type=Path)
    cli.add_argument('--cards-only',action='store_true')
    cli.add_argument('--cpu-node', default='iot-cw-vm')
    cli.add_argument('--loopback-start-port', default=28080, type=int)
    args = cli.parse_args()
    if args.cards_only and not args.resume:
        cli.error('Cards-only requires prior positive model and tool receipts')
    if not 1024 <= args.loopback_start_port <= 65532:
        cli.error('Choose four consecutive nonprivileged loopback ports')
    run(args)
