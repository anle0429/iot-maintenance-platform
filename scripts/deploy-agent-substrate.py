"""Install the source-pinned sandbox runtime on the retained SSD and connect the private agent controller."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import json
import os
import re
import shutil
import subprocess
import yaml


def run(root, source, run_name, ansible):
    """Retain native installation and idempotence receipts without exporting generated bootstrap or database secrets."""
    root = root.resolve(); source = source.resolve()
    if os.name != 'posix' or os.geteuid() != 0 or not source.is_relative_to(root / 'data/llm-source'):
        raise ValueError('Run a sealed source on the existing Linux lab node with sudo')
    if not re.fullmatch(r'agent-sandbox-[a-z0-9-]+', run_name):
        raise ValueError('Use a new agent-sandbox run name')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    for name, digest in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('The sealed agent source differs')
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    remaining = (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds()
    memory = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))) * 1024
    if remaining < 2100 or memory < 16 * 1024**3 or shutil.disk_usage('/srv/iot-fast').free < 50 * 1024**3:
        raise ValueError('Require 35 approved session minutes, 16 GiB RAM and 50 GiB SSD before installing')
    output = root / 'outputs' / run_name; output.mkdir(exist_ok=False)
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'
    os.environ['HOME'] = str(root / 'data/runtime/agent-operator-home')
    os.environ['PATH'] = str(ansible.parent) + ':' + os.environ['PATH']
    k = ['/usr/local/bin/k3s', 'kubectl']; helm = str(root / 'data/runtime/kubernetes-assets-27/helm/helm')

    def save(name, value):
        """Write one new strict JSON receipt, preserving any prior run directory."""
        (output / name).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')

    def command(label, args, timeout=600, payload=None):
        """Capture only commands whose output excludes Secret payloads and credential-bearing stdin."""
        result = subprocess.run(args, input=payload, capture_output=True, timeout=timeout)
        (output / (label + '.stdout')).write_bytes(result.stdout)
        (output / (label + '.stderr')).write_bytes(result.stderr)
        (output / (label + '.exit')).write_text(str(result.returncode) + '\n')
        if result.returncode:
            raise RuntimeError(label + ' failed; preserve the run and inspect its receipts')
        return result.stdout

    save('source.json', seal)
    save('budget.json', {'remaining_session_seconds': remaining, 'available_ram_bytes': memory,
                        'ssd_free_bytes': shutil.disk_usage('/srv/iot-fast').free,
                        'planned_pvc_gib': 22, 'reserved_worker_pools': 3, 'workers_per_pool': 2,
                        'new_cloud_resources': False})
    node_before = command('node-before', k + ['describe', 'node', 'iot-cw-vm']).decode()
    allocated = re.search(r'^\s+cpu\s+(\d+)m\s+\(', node_before, re.MULTILINE)
    if allocated is None or int(allocated.group(1)) + 1100 > 8000:
        raise ValueError('Keep 1100 millicores for substrate and six workers within the existing eight CPUs')
    lock = json.loads((source / 'infra/agents/versions.lock.json').read_bytes())
    charts = {}
    for row in lock['charts']:
        path = root / 'outputs/agent-foundation-research' / (row['name'] + '-' + row['version'] + '.tgz')
        if sha256(path.read_bytes()).hexdigest() != row['sha256'] or path.stat().st_size != row['bytes']:
            raise ValueError('Official chart archive differs: ' + row['name'])
        charts[row['name']] = path
    playbook = str(source / 'infra/ansible/agent-sandbox-storage.yml')
    command('ssd-first', [str(ansible), '-i', 'localhost,', playbook])
    command('ssd-second', [str(ansible), '-i', 'localhost,', playbook])
    existing = command('namespace-before', k + ['get', 'namespace', 'ate-system', '--ignore-not-found', '-o', 'json'])
    if existing.strip():
        ns = json.loads(existing)
        if ns['metadata'].get('labels', {}).get('iot.coursework/module') != 'agent-substrate':
            raise ValueError('Preserve an unrelated substrate namespace')
    command('namespace-apply', k + ['apply', '-f', '-'], payload=json.dumps({'apiVersion': 'v1', 'kind': 'Namespace',
        'metadata': {'name': 'ate-system', 'labels': {'iot.coursework/module': 'agent-substrate'}}}).encode())
    credentials = json.loads((root / 'data/runtime/secrets/agent-platform.json').read_bytes())
    secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'iot-substrate-object-store', 'namespace': 'ate-system'},
        'type': 'Opaque', 'stringData': {'access': credentials['snapshot_access'], 'secret': credentials['snapshot_secret']}}
    command('object-store-secret', k + ['apply', '-f', '-'], payload=json.dumps(secret).encode())
    renderer = source / 'scripts/render-agent-substrate.py'; renderer.chmod(0o755)
    command('substrate-crds', [helm, 'upgrade', '--install', 'substrate-crds', str(charts['substrate-crds']),
        '-n', 'ate-system', '--wait', '--timeout', '5m'])
    command('substrate-install', [helm, 'upgrade', '--install', 'substrate', str(charts['substrate']),
        '-n', 'ate-system', '-f', str(source / 'infra/agents/substrate-values.yaml'),
        '--post-renderer', str(renderer), '--wait', '--wait-for-jobs', '--timeout', '12m'], timeout=850)
    ca = json.loads(command('public-ca', k + ['get', 'configmap', 'ateapi-ca', '-n', 'ate-system', '-o', 'json']))
    command('controller-ca', k + ['apply', '-f', '-'], payload=json.dumps({'apiVersion': 'v1', 'kind': 'ConfigMap',
        'metadata': {'name': 'iot-substrate-ca', 'namespace': 'iot-agents'}, 'data': ca['data']}).encode())
    values = yaml.safe_load((source / 'infra/agents/kagent-values.yaml').read_text())
    controller = values['controller']
    controller['substrate'].update(enabled=True, ateApiEndpoint='dns:///api.ate-system.svc:443',
        atenetRouterURL='http://atenet-router.ate-system.svc:80', ateApiInsecure=False,
        defaultWorkerPool={'namespace': 'iot-agents', 'name': 'iot-coordinator-pool'})
    controller['volumes'].append({'name': 'substrate-ca', 'configMap': {'name': 'iot-substrate-ca'}})
    controller['volumeMounts'].append({'name': 'substrate-ca', 'mountPath': '/etc/ate-ca', 'readOnly': True})
    controller['env'] = [{'name': 'SSL_CERT_FILE', 'value': '/etc/ate-ca/ca.crt'}]
    path = output / 'controller-values.json'; path.write_text(json.dumps(values, indent=2) + '\n')
    command('controller-integration', [helm, 'upgrade', 'kagent', str(charts['kagent']), '-n', 'iot-agents',
        '-f', str(path), '--atomic', '--wait', '--timeout', '6m'], timeout=450)
    command('resources-after', k + ['get', 'deployment,daemonset,statefulset,pod,pvc,service,job', '-n', 'ate-system', '-o', 'json'])
    command('controller-after', k + ['get', 'deployment', 'kagent-controller', '-n', 'iot-agents', '-o', 'json'])
    command('node-after', k + ['describe', 'node', 'iot-cw-vm'])
    for path in output.rglob('*'):
        if path.is_file() and any(v.encode() in path.read_bytes() for v in credentials.values()):
            raise ValueError('Do not publish captured private credentials')
    report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'run': run_name,
              'runtime': 'Substrate 0.0.9', 'controller_tls_verification': True,
              'scope': 'Sandbox control plane installed and connected. Actual actor execution, isolation and warmup remain separate checks.'}
    save('verification.json', report); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--ansible', type=Path, required=True)
    args = parser.parse_args(); run(args.runtime_root, args.source_root, args.run, args.ansible)
