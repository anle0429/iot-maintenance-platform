"""Adjust CPU-agent response budgets while preserving the live sandbox integration and private model identity."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import json
import os
import re
import subprocess
import yaml


def run(root, source, run_name):
    """Apply only the source-pinned generation/timeout settings to the existing native Helm release."""
    root = root.resolve(); source = source.resolve()
    if os.name != 'posix' or os.geteuid() != 0 or not re.fullmatch(r'agent-model-[a-z0-9-]+', run_name):
        raise ValueError('Use a new agent-model run on the retained Linux node')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    if not source.is_relative_to(root / 'data/llm-source') or any(sha256((source / n).read_bytes()).hexdigest() != d for n, d in seal['files'].items()):
        raise ValueError('Use an unchanged sealed project source')
    output = root / 'outputs' / run_name; output.mkdir(exist_ok=False)
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'; os.environ['HOME'] = str(root / 'data/runtime/agent-operator-home')
    helm = str(root / 'data/runtime/kubernetes-assets-27/helm/helm'); k = ['/usr/local/bin/k3s', 'kubectl']

    def command(label, args):
        """Record command status and public-reference values without reading Secret objects."""
        result = subprocess.run(args, capture_output=True, timeout=600)
        (output / (label + '.stdout')).write_bytes(result.stdout); (output / (label + '.stderr')).write_bytes(result.stderr)
        (output / (label + '.exit')).write_text(str(result.returncode) + '\n')
        if result.returncode:
            raise RuntimeError(label + ' failed')
        return result.stdout

    (output / 'source.json').write_text(json.dumps(seal, indent=2) + '\n')
    current = json.loads(command('before-values', [helm, 'get', 'values', 'kagent', '-n', 'iot-agents', '-o', 'json']))
    desired = yaml.safe_load((source / 'infra/agents/kagent-values.yaml').read_text())
    if not current['controller']['substrate']['enabled'] or current['controller']['substrate']['ateApiInsecure']:
        raise ValueError('Preserve the existing TLS-verified substrate integration')
    if current['providers']['openAI']['model'] != desired['providers']['openAI']['model']:
        raise ValueError('Keep the same internal model')
    current['providers']['openAI']['config'] = desired['providers']['openAI']['config']
    current['controller']['a2aClientTimeout'] = desired['controller']['a2aClientTimeout']
    path = output / 'values.json'; path.write_text(json.dumps(current, indent=2) + '\n')
    chart = root / 'outputs/agent-foundation-research/kagent-0.10.3.tgz'
    lock = json.loads((source / 'infra/agents/versions.lock.json').read_bytes())
    expected = next(row for row in lock['charts'] if row['name'] == 'kagent')
    if sha256(chart.read_bytes()).hexdigest() != expected['sha256']:
        raise ValueError('Kagent chart archive differs')
    command('upgrade', [helm, 'upgrade', 'kagent', str(chart), '-n', 'iot-agents', '-f', str(path), '--atomic', '--wait', '--timeout', '5m'])
    config = json.loads(command('model-config', k + ['get', 'modelconfig', 'default-model-config', '-n', 'iot-agents', '-o', 'json']))
    report = {'passed': config['spec']['openAI'] == desired['providers']['openAI']['config'],
        'checked_utc': datetime.now(timezone.utc).isoformat(), 'openai_compatible_local_config': config['spec']['openAI'],
        'model': config['spec']['model'], 'controller_a2a_timeout': current['controller']['a2aClientTimeout'],
        'scope': 'CPU generation timeout and output cap changed; model, sandbox TLS, database and tool policy preserved.'}
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True); parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--run', required=True); args = parser.parse_args(); run(args.runtime_root, args.source_root, args.run)
