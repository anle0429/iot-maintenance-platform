"""Increase the retained CPU model's context for complete agent tool results, preserving weights and compute limits."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import build_opener, ProxyHandler
import argparse
import json
import os
import re
import shutil
import subprocess


def run(root, source, run_name):
    """Measure reserves, replace the one lab model briefly, and read back actual model properties and Kubernetes arguments."""
    root = root.resolve(); source = source.resolve()
    if os.name != 'posix' or os.geteuid() != 0 or not re.fullmatch(r'agent-context-[a-z0-9-]+', run_name):
        raise ValueError('Run a new agent-context identifier on the retained Linux node')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    if not source.is_relative_to(root / 'data/llm-source') or any(sha256((source / n).read_bytes()).hexdigest() != d for n, d in seal['files'].items()):
        raise ValueError('Use unchanged sealed source')
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    remaining = (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds()
    ram = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))) * 1024
    if remaining < 1200 or ram < 16 * 1024**3 or shutil.disk_usage('/srv/iot-fast').free < 25 * 1024**3:
        raise ValueError('Require 20 approved session minutes, 16 GiB RAM and 25 GiB SSD')
    output = root / 'outputs' / run_name; output.mkdir(exist_ok=False)
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'; os.environ['HOME'] = str(root / 'data/runtime/agent-operator-home')
    os.environ['PATH'] = str(Path(os.sys.executable).parent) + ':' + os.environ['PATH']
    helm = str(root / 'data/runtime/kubernetes-assets-27/helm/helm'); k = ['/usr/local/bin/k3s', 'kubectl']

    def command(label, args):
        """Record native status and public model configuration without model weights or credential objects."""
        result = subprocess.run(args, capture_output=True, timeout=480)
        (output / (label + '.stdout')).write_bytes(result.stdout); (output / (label + '.stderr')).write_bytes(result.stderr)
        (output / (label + '.exit')).write_text(str(result.returncode) + '\n')
        if result.returncode:
            raise RuntimeError(label + ' failed')
        return result.stdout

    (output / 'source.json').write_text(json.dumps(seal, indent=2) + '\n')
    (output / 'budget.json').write_text(json.dumps({'remaining_session_seconds': remaining, 'available_ram_bytes': ram,
        'ssd_free_bytes': shutil.disk_usage('/srv/iot-fast').free, 'estimated_additional_kv_bytes': 4096 * 28 * 4 * 128 * 2 * 2,
        'new_cloud_resources': False, 'rollout': 'Brief Recreate interruption; the existing node cannot admit two model CPU reservations.'}, indent=2) + '\n')
    values = json.loads(command('before-values', [helm, 'get', 'values', 'iot-model', '-n', 'iot-llm', '-o', 'json']))
    if values['servedModel'] != 'iot-qwen2.5-7b' or values['contextTokens'] != 4096:
        raise ValueError('Preserve a different current model/context configuration')
    command('before-deployment', k + ['get', 'deployment', 'iot-model', '-n', 'iot-llm', '-o', 'json'])
    values['contextTokens'] = 8192; path = output / 'values.json'; path.write_text(json.dumps(values, indent=2) + '\n')
    renderer = source / 'scripts/render-single-node-model.py'; renderer.chmod(0o755)
    command('upgrade', [helm, 'upgrade', 'iot-model', str(source / 'infra/charts/iot-llm'), '-n', 'iot-llm',
        '-f', str(path), '--post-renderer', str(renderer), '--atomic', '--wait', '--timeout', '6m'])
    deployment = json.loads(command('after-deployment', k + ['get', 'deployment', 'iot-model', '-n', 'iot-llm', '-o', 'json']))
    svc = json.loads(command('service', k + ['get', 'service', 'iot-model', '-n', 'iot-llm', '-o', 'json']))
    opener = build_opener(ProxyHandler({}))
    with opener.open('http://' + svc['spec']['clusterIP'] + ':8080/props', timeout=30) as response:
        props = response.read()
    (output / 'model-props.json').write_bytes(props)
    args = deployment['spec']['template']['spec']['containers'][0]['args']
    report = {'passed': args[args.index('--ctx-size') + 1] == '8192' and deployment['status']['availableReplicas'] == 1,
        'checked_utc': datetime.now(timezone.utc).isoformat(), 'context_tokens_before': 4096, 'context_tokens_after': 8192,
        'model': values['servedModel'], 'model_image': values['image'], 'resources': values['resources'],
        'scope': 'Same weights, image, CPU and memory limits; larger context and a documented single-node rollout interruption.'}
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True); parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--run', required=True); args = parser.parse_args(); run(args.runtime_root, args.source_root, args.run)
