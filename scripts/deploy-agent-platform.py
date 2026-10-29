"""Install source-pinned private agent control services on the retained lab node, preserving data and credentials."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import urlopen
import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import yaml


def run(root, source, run_name):
    """Verify actual reserves and source bytes, then deploy persistent storage, kagent and its private catalog."""
    root = root.resolve(); source = source.resolve()
    if os.name != 'posix' or os.geteuid() != 0 or not source.is_relative_to(root / 'data/llm-source'):
        raise ValueError('Use a sealed source on the retained Linux VM with sudo')
    if not re.fullmatch(r'agent-core-[a-z0-9-]+', run_name):
        raise ValueError('Use a distinct agent-core run name')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    for name, digest in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Agent source seal differs')
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    remaining = (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds()
    memory = int(next(row.split()[1] for row in Path('/proc/meminfo').read_text().splitlines() if row.startswith('MemAvailable:'))) * 1024
    if remaining < 2100 or memory < 16 * 1024**3 or shutil.disk_usage('/srv/iot-fast').free < 40 * 1024**3:
        raise ValueError('Require 35 session minutes, 16 GiB available RAM and 40 GiB SSD reserve')
    output = root / 'outputs' / run_name; output.mkdir(exist_ok=False)
    (output / 'source.json').write_text(json.dumps(seal, indent=2) + '\n')
    (output / 'budget.json').write_text(json.dumps({'remaining_session_seconds': remaining, 'available_ram_bytes': memory,
        'ssd_free_bytes': shutil.disk_usage('/srv/iot-fast').free, 'new_cloud_resources': False}, indent=2) + '\n')
    k = ['/usr/local/bin/k3s', 'kubectl']; helm = str(root / 'data/runtime/kubernetes-assets-27/helm/helm')
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'
    os.environ['HOME'] = str(root / 'data/runtime/agent-operator-home')
    Path(os.environ['HOME']).mkdir(mode=0o700, exist_ok=True)

    def command(label, args, timeout=600, payload=None):
        """Retain native consoles and exits while never recording credential-bearing stdin."""
        if (output / (label + '.stdout')).exists():
            raise ValueError('Preserve the existing receipt: ' + label)
        result = subprocess.run(args, input=payload, capture_output=True, timeout=timeout)
        (output / (label + '.stdout')).write_bytes(result.stdout)
        (output / (label + '.stderr')).write_bytes(result.stderr)
        (output / (label + '.exit')).write_text(str(result.returncode) + '\n')
        if result.returncode:
            raise RuntimeError(label + ' failed; inspect the retained run before changing anything')
        return result.stdout

    def get(label, args):
        """Read Kubernetes objects whose bodies contain no Secret values."""
        return json.loads(command(label, k + args + ['-o', 'json']))

    def secret(label, name, values):
        """Send credential data only through stdin and record only Kubernetes' object-name acknowledgment."""
        body = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': name, 'namespace': 'iot-agents'},
                'type': 'Opaque', 'stringData': values}
        command(label, k + ['apply', '-f', '-'], payload=json.dumps(body).encode())

    namespace = get('namespace', ['get', 'namespace', 'iot-agents'])
    if namespace['metadata'].get('labels', {}).get('iot.coursework/module') != 'mcp':
        raise ValueError('The namespace is not the retained owned MCP namespace')
    command('node-before', k + ['describe', 'node', 'iot-cw-vm'])
    command('helm-before', [helm, 'list', '-n', 'iot-agents', '-o', 'json'])
    lock = json.loads((source / 'infra/agents/versions.lock.json').read_bytes())
    assets = root / 'outputs/agent-foundation-research'
    charts = {}
    for row in lock['charts']:
        path = assets / (row['name'] + '-' + row['version'] + '.tgz')
        if not path.is_file() or path.stat().st_size != row['bytes'] or sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Official chart archive differs: ' + row['name'])
        charts[row['name']] = path
    private = root / 'data/runtime/secrets/agent-platform.json'
    if private.exists():
        credentials = json.loads(private.read_bytes())
    else:
        credentials = {key: secrets.token_hex(32) for key in ['administrator', 'kagent', 'registry', 'local_model', 'snapshot_access', 'snapshot_secret']}
        descriptor = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(credentials, stream)
    if set(credentials) != {'administrator', 'kagent', 'registry', 'local_model', 'snapshot_access', 'snapshot_secret'}:
        raise ValueError('Preserve the different existing credential file')
    if any(not re.fullmatch(r'[0-9a-f]{64}', value) for value in credentials.values()):
        raise ValueError('Unexpected credential format')
    secret('database-bootstrap', 'iot-agent-database-bootstrap', {key: credentials[key] for key in ['administrator', 'kagent', 'registry']})
    for role in ['kagent', 'registry']:
        url = 'postgres://iot_' + role + ':' + credentials[role] + '@iot-agent-database.iot-agents.svc:5432/iot_' + role + '?sslmode=disable'
        secret(role + '-database', 'iot-' + role + '-database', {'url': url})
    secret('local-model-client', 'iot-local-model-client', {'API_KEY': credentials['local_model']})
    mcp = json.loads((root / 'data/runtime/secrets/mcp-tools.json').read_bytes())
    secret('agent-tool-headers', 'iot-agent-tool-headers', {role: 'Bearer ' + mcp[role] for role in ['retrieval', 'drift']})
    command('postgres-pull', ['docker', 'pull', lock['postgres_image']], timeout=600)
    image = json.loads(command('postgres-inspect', ['docker', 'image', 'inspect', lock['postgres_image']]))[0]
    if image['Architecture'] != 'amd64' or image['Os'] != 'linux':
        raise ValueError('Unexpected PostgreSQL architecture')
    identity = command('postgres-uid', ['docker', 'run', '--rm', '--network', 'none', '--cpus=.25', '--memory=64m',
        '--entrypoint', 'id', lock['postgres_image'], 'postgres']).decode()
    if 'uid=70(postgres)' not in identity or 'gid=70(postgres)' not in identity:
        raise ValueError('The official image does not use the expected unprivileged identity')
    image_ref = next(name for name in image['RepoDigests'] if name.startswith('postgres@sha256:'))
    database_values = output / 'database-values.json'
    database_values.write_text(json.dumps({'image': image_ref}, indent=2) + '\n')
    command('database-install', [helm, 'upgrade', '--install', 'iot-agent-store', str(source / 'infra/charts/iot-agent-store'),
        '-n', 'iot-agents', '-f', str(database_values), '--wait', '--timeout', '5m'], timeout=420)
    command('kagent-crds', [helm, 'upgrade', '--install', 'kagent-crds', str(charts['kagent-crds']), '-n', 'iot-agents',
        '--set', 'kmcp.enabled=false', '--set', 'substrate.enabled=false', '--wait', '--timeout', '5m'])
    for label, release, chart, values in [('kagent', 'kagent', 'kagent', 'kagent-values.yaml'),
        ('registry', 'iot-agent-registry', 'agentregistry', 'registry-values.yaml')]:
        command(label + '-install', [helm, 'upgrade', '--install', release, str(charts[chart]), '-n', 'iot-agents',
            '-f', str(source / 'infra/agents' / values), '--atomic', '--wait', '--timeout', '8m'], timeout=600)
    config = get('model-config', ['get', 'modelconfig', 'default-model-config', '-n', 'iot-agents'])
    if config['spec']['model'] != lock['local_model'] or config['spec']['openAI']['baseUrl'] != lock['custom_model_base_url']:
        raise ValueError('Global model configuration differs from the existing local model')
    items = get('resources-after', ['get', 'deployment,pod,service,pvc,networkpolicy', '-n', 'iot-agents'])['items']
    services = {row['metadata']['name']: row for row in items if row['kind'] == 'Service'}
    observed = {}
    for name, path, port in [('kagent-controller', '/health', 8083), ('kagent-ui', '/', 8080), ('iot-agent-registry', '/', 12121)]:
        url = 'http://' + services[name]['spec']['clusterIP'] + ':' + str(port) + path
        for attempt in range(30):
            try:
                with urlopen(url, timeout=10) as response:
                    observed[name] = {'status': response.status, 'bytes': len(response.read()), 'url': url}
                break
            except OSError:
                if attempt == 29:
                    raise
                time.sleep(2)
    (output / 'http-readback.json').write_text(json.dumps(observed, indent=2) + '\n')
    command('node-after', k + ['describe', 'node', 'iot-cw-vm'])
    verification = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'run': run_name,
        'postgres_image': image_ref, 'model': config['spec']['model'], 'model_config_uid': config['metadata']['uid'],
        'registry_ready': True, 'controller_ready': True, 'ui_ready': True,
        'scope': 'Persistent private catalog and controller plus applied custom model configuration. Agent execution, registry publication, sandbox, autoscaling and warmup require separate native proof.'}
    for path in output.rglob('*'):
        if path.is_file() and any(value.encode() in path.read_bytes() for value in list(credentials.values()) + list(mcp.values())):
            raise ValueError('A private credential appeared in a captured receipt; do not publish it')
    (output / 'verification.json').write_text(json.dumps(verification, indent=2) + '\n')
    print(json.dumps(verification, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--run', required=True)
    args = parser.parse_args(); run(args.runtime_root, args.source_root, args.run)
