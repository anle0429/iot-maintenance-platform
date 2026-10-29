"""Archive selected terminal agent runs, sealed sources and native readbacks while excluding credentials and runtime state."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import tarfile


def compiled_model_budgets(kubectl):
    """Read only selected non-secret settings from the generated config Secrets, retaining hashes instead of their contents."""
    observed = {}
    for role in ['retrieval', 'drift', 'coordinator']:
        name = 'iot-' + role
        body = json.loads(subprocess.check_output(kubectl + ['get', 'secret', name, '-n', 'iot-agents', '-o', 'json']))
        raw = base64.b64decode(body['data']['config.json']); config = json.loads(raw); selected = {}

        def visit(value, path=''):
            """Expose only literal model endpoint, output-cap and timeout settings, never headers or credentials."""
            if isinstance(value, dict):
                for key, child in value.items():
                    here = path + '/' + key
                    if key in {'timeout', 'max_tokens', 'maxTokens', 'base_url', 'baseUrl'} and isinstance(child, (str, int, float)):
                        selected[here] = child
                    elif isinstance(child, (dict, list)):
                        visit(child, here)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, path + '/' + str(index))
        visit(config)
        observed[name] = {'secret_uid': body['metadata']['uid'], 'resource_version': body['metadata']['resourceVersion'],
                          'config_sha256': sha256(raw).hexdigest(), 'selected_public_settings': selected}
    return observed


def run(root, run_name, chat_run=None, chat_source=None):
    """Capture originals only after authoritative unit checks, refusing private values or unexpected source members."""
    root = root.resolve()
    if os.name != 'posix' or os.geteuid() != 0 or not re.fullmatch(r'agent-capture-[a-z0-9-]+', run_name):
        raise ValueError('Run a new agent-capture identifier on the retained Linux node with sudo')
    base = root / 'outputs' / run_name; base.mkdir(exist_ok=False)
    private = []
    for name in ['agent-platform.json', 'mcp-tools.json']:
        values = json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values()
        for value in values:
            private.extend([value.encode(), base64.b64encode(value.encode()), base64.b64encode(('Bearer ' + value).encode())])

    def keep(path, data):
        """Refuse credential-bearing bytes before saving a public candidate receipt."""
        if any(value in data for value in private):
            raise ValueError('Do not publish a private value in ' + str(path.relative_to(base)))
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)

    def command(label, arguments):
        """Retain native stdout, stderr and exact exit status without shell commands or Secret reads."""
        result = subprocess.run(arguments, capture_output=True, timeout=120)
        keep(base / 'readback' / (label + '.stdout'), result.stdout)
        keep(base / 'readback' / (label + '.stderr'), result.stderr)
        keep(base / 'readback' / (label + '.exit'), (str(result.returncode) + '\n').encode())
        return result

    phases = [
        ('core', 'agent-core-29a', 'agent-core-29a'),
        ('substrate', 'agent-sandbox-29a', 'agent-sandbox-29a'),
        ('invalid-schema', 'agent-workloads-29a', 'agent-workloads-29a'),
        ('selector-failure', 'agent-workloads-29b', 'agent-workloads-29b'),
        ('selector-repair', 'agent-workloads-29c', 'agent-workloads-29c'),
        ('deployment', 'agent-workloads-29d', 'agent-workloads-29d'),
        ('catalog-and-wire-failure', 'agent-acceptance-29a', 'agent-acceptance-29a'),
        ('session-id-failure', 'agent-chat-29a', 'agent-chat-29a'),
        ('router-failure', 'agent-chat-29b', 'agent-chat-29b'),
        ('generation-timeout', 'agent-chat-29c', 'agent-chat-29b'),
        ('model-budget', 'agent-model-29a', 'agent-model-29a'),
        ('truncated-answer', 'agent-chat-29d', 'agent-chat-29d'),
        ('model-context', 'agent-context-29a', 'agent-context-29a'),
    ]
    if chat_run:
        if not re.fullmatch(r'agent-chat-[a-z0-9-]+', chat_run):
            raise ValueError('Use an existing agent-chat run')
        source_run = chat_source or chat_run
        if not re.fullmatch(r'agent-chat-[a-z0-9-]+', source_run):
            raise ValueError('Supply the exact sealed source used by the native chat unit')
        phases.append(('chat', chat_run, source_run))
    for prefix, native_run, source_run in phases:
        result = command(prefix + '-unit', ['systemctl', 'show', 'iot-' + native_run,
            '--property=Id,ActiveState,SubState,Result,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp,InvocationID,ExecStart'])
        if result.returncode or b'ActiveState=active\n' in result.stdout or b'ActiveState=activating\n' in result.stdout:
            raise ValueError('Wait for the actual native unit to finish: ' + native_run)
        command(prefix + '-journal', ['journalctl', '-u', 'iot-' + native_run, '--no-pager', '-o', 'short-iso'])
        native = root / 'outputs' / native_run
        for path in sorted(native.rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to(native)
            if any(part.startswith('.') for part in relative.parts) or path.suffix in {'.db', '.sqlite', '.sqlite3'}:
                raise ValueError('Unexpected private runtime/cache member')
            keep(base / prefix / 'native' / relative, path.read_bytes())
        sealed = root / 'data/llm-source' / source_run; seal = json.loads((sealed / 'manifest.json').read_bytes())
        keep(base / prefix / 'source' / 'manifest.json', (sealed / 'manifest.json').read_bytes())
        for name, digest in seal['files'].items():
            path = (sealed / 'tree' / name).resolve()
            if not path.is_relative_to(sealed / 'tree') or sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('Source seal differs for ' + prefix)
            keep(base / prefix / 'source' / 'tree' / name, path.read_bytes())
    k = ['/usr/local/bin/k3s', 'kubectl']
    for suffix in ['29a', '29b', '29c']:
        native_run = 'agent-sandbox-probe-' + suffix
        result = command(native_run + '-unit', ['systemctl', 'show', 'iot-' + native_run,
            '--property=Id,ActiveState,SubState,Result,ExecMainStatus,InvocationID'])
        if b'ActiveState=active\n' in result.stdout:
            raise ValueError('Wait for the actual sandbox diagnostic to finish')
        folder = root / 'outputs' / native_run
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                keep(base / native_run / 'native' / path.relative_to(folder), path.read_bytes())
        keep(base / native_run / 'source.py', Path('/tmp/iot-agent-sandbox-probe-' + suffix + '.py').read_bytes())
    keep(base / 'capture-operator.py', Path(__file__).read_bytes())
    keep(base / 'readback/compiled-model-budget.json', (json.dumps(compiled_model_budgets(k), indent=2) + '\n').encode())
    for label, kinds, namespace in [
        ('agents', 'sandboxagents,remotemcpservers,workerpools,pods,services,networkpolicies,modelconfigs', 'iot-agents'),
        ('substrate', 'deployments,daemonsets,statefulsets,pods,services,pvc,jobs', 'ate-system'),
        ('templates', 'actortemplates', 'iot-agents')]:
        result = command(label, k + ['get', kinds, '-n', namespace, '-o', 'json'])
        if result.returncode:
            raise ValueError('Native readback failed: ' + label)
    command('node', k + ['describe', 'node', 'iot-cw-vm'])
    command('filesystem', ['df', '-B1', '/srv/iot-fast', '/srv/iot-data', '/var/lib/ateom-gvisor'])
    command('memory', ['free', '-b'])
    items = json.loads((base / 'readback/agents.stdout').read_bytes())['items']
    services = {s['metadata']['name']: s for s in items if s['kind'] == 'Service'}
    opener = build_opener(ProxyHandler({}))
    endpoints = [('substrate-status.json', 'kagent-controller', 8083, '/api/substrate/status'),
                 ('model-configs.json', 'kagent-controller', 8083, '/api/modelconfigs'),
                 ('catalog.json', 'iot-agent-registry', 12121, '/v0/agents')]
    for name, service, port, route in endpoints:
        url = 'http://' + services[service]['spec']['clusterIP'] + ':' + str(port) + route
        with opener.open(url, timeout=30) as response:
            keep(base / 'readback' / name, response.read())
    inventory = {str(p.relative_to(base)): {'bytes': p.stat().st_size, 'sha256': sha256(p.read_bytes()).hexdigest()}
                 for p in sorted(base.rglob('*')) if p.is_file()}
    keep(base / 'capture.json', (json.dumps({'checked_utc': datetime.now(timezone.utc).isoformat(), 'run': run_name,
        'files': inventory, 'credential_scan_passed': True, 'scope': 'Selected native originals; no model, database, kubeconfig, Secret value or generated operator home.'}, indent=2) + '\n').encode())
    archive = base.with_suffix('.tar')
    with tarfile.open(archive, 'w') as stream:
        for path in sorted(base.rglob('*')):
            if path.is_file():
                stream.add(path, arcname=str(path.relative_to(base)))
    metadata = {'archive': str(archive), 'bytes': archive.stat().st_size,
                'sha256': sha256(archive.read_bytes()).hexdigest(), 'payload_files': len(inventory) + 1}
    archive.with_suffix('.archive.json').write_text(json.dumps(metadata, indent=2) + '\n'); print(json.dumps(metadata, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--chat-run')
    parser.add_argument('--chat-source')
    args = parser.parse_args(); run(args.runtime_root, args.run, args.chat_run, args.chat_source)
