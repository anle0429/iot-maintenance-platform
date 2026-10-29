"""Deploy source-sealed IoT sandbox agents and retain actual readiness, worker and tool-discovery receipts."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import json
import os
import re
import shutil
import subprocess


def run(root, source, run_name):
    """Install three bounded agent roles, checking native CR readiness separately from Helm's success status."""
    root = root.resolve(); source = source.resolve()
    if os.name != 'posix' or os.geteuid() != 0 or not source.is_relative_to(root / 'data/llm-source'):
        raise ValueError('Use a sealed source on the retained Linux VM with sudo')
    if not re.fullmatch(r'agent-workloads-[a-z0-9-]+', run_name):
        raise ValueError('Use a new agent-workloads run name')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    for name, expected in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Agent source seal differs')
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    remaining = (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds()
    if remaining < 1200 or shutil.disk_usage('/srv/iot-fast').free < 25 * 1024**3:
        raise ValueError('Require 20 approved session minutes and 25 GiB SSD after runtime installation')
    output = root / 'outputs' / run_name; output.mkdir(exist_ok=False)
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'
    os.environ['HOME'] = str(root / 'data/runtime/agent-operator-home')
    k = ['/usr/local/bin/k3s', 'kubectl']; helm = str(root / 'data/runtime/kubernetes-assets-27/helm/helm')

    def command(label, args, timeout=600, payload=None):
        """Retain original public-reference object consoles and check actual exit codes."""
        result = subprocess.run(args, input=payload, capture_output=True, timeout=timeout)
        (output / (label + '.stdout')).write_bytes(result.stdout)
        (output / (label + '.stderr')).write_bytes(result.stderr)
        (output / (label + '.exit')).write_text(str(result.returncode) + '\n')
        if result.returncode:
            raise RuntimeError(label + ' failed; preserve this run before attempting a repair')
        return result.stdout

    (output / 'source.json').write_text(json.dumps(seal, indent=2) + '\n')
    command('runtime-before', k + ['get', 'deployments,daemonsets,statefulsets', '-n', 'ate-system', '-o', 'json'])
    chart = str(source / 'infra/charts/iot-agents')
    command('lint', [helm, 'lint', chart])
    rendered = command('rendered', [helm, 'template', 'iot-agents', chart, '-n', 'iot-agents'])
    command('server-validation', k + ['apply', '--dry-run=server', '-f', '-'], payload=rendered)
    command('install', [helm, 'upgrade', '--install', 'iot-agents', chart, '-n', 'iot-agents',
        '--atomic', '--wait', '--timeout', '8m'])
    # The runtime copies WorkerPool metadata labels when it handles a worker Pod event.
    # Notify only our six workers after a selector-label repair, avoiding the periodic five-minute resync.
    command('worker-label-refresh', k + ['annotate', 'pods', '-n', 'iot-agents', '-l',
        'ate.dev/worker-pool in (iot-retrieval-pool,iot-drift-pool,iot-coordinator-pool)',
        'iot.coursework/worker-label-refresh=' + sha256(json.dumps(seal, sort_keys=True).encode()).hexdigest()[:16], '--overwrite'])
    command('agents-ready', k + ['wait', 'sandboxagents', '--all', '-n', 'iot-agents', '--for=condition=Ready', '--timeout=600s'], timeout=660)
    command('agents-after', k + ['get', 'sandboxagents,remotemcpservers,workerpools,pods', '-n', 'iot-agents', '-o', 'json'])
    command('node-after', k + ['describe', 'node', 'iot-cw-vm'])
    private = []
    for name in ['agent-platform.json', 'mcp-tools.json']:
        private.extend(json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values())
    for path in output.rglob('*'):
        if path.is_file() and any(value.encode() in path.read_bytes() for value in private):
            raise ValueError('Do not publish a credential-bearing receipt')
    report = {'passed': True, 'run': run_name, 'checked_utc': datetime.now(timezone.utc).isoformat(),
        'roles': ['retrieval', 'drift', 'coordinator'], 'workers_per_role': 2,
        'scope': 'Native Ready conditions and installed per-role worker capacity. Actual chat, tool use, isolation, scale and registry publication require separate checks.'}
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--run', required=True)
    args = parser.parse_args(); run(args.runtime_root, args.source_root, args.run)
