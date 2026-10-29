"""Atomically switch the retained private backend to a source-pinned larger CPU candidate for grounding evaluation."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from hashlib import sha256
import argparse
import importlib.util
import json
import os
import shutil
import subprocess


def deploy(args):
    """Download verified model shards on the existing SSD and retain both deployment inputs and actual responses."""
    root = args.runtime_root.resolve(); source = args.source_root.resolve(); output = args.output.resolve()
    if not source.is_relative_to(root / 'data') or not output.is_relative_to(root / 'outputs'): raise ValueError('Use the retained lab source/output directories')
    spec = importlib.util.spec_from_file_location('inference_deploy', source / 'scripts/deploy-llm-inference.py')
    shared = importlib.util.module_from_spec(spec); spec.loader.exec_module(shared)
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    budget = shared.resource_gate(root, datetime.fromisoformat(session['auto_stop_utc']))
    if budget['available_memory_bytes'] < 20 * 1024**3: raise ValueError('At least 20 GiB available RAM is required for the rolling candidate change')
    if output.exists(): raise ValueError('Preserve the previous output; choose a new directory')
    output.mkdir(parents=True)
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    for name, digest in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != digest: raise ValueError('Source seal differs')
    (output / 'source.json').write_text(json.dumps(seal, indent=2)+'\n')
    (output / 'budget.json').write_text(json.dumps(budget, indent=2)+'\n')
    lock = json.loads((source / 'infra/llm/assistant.lock.json').read_bytes())
    (output / 'model-lock.json').write_text(json.dumps(lock, indent=2)+'\n')
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'; k = ['/usr/local/bin/k3s', 'kubectl']
    helm = str(root / 'data/runtime/kubernetes-assets-27/helm/helm')

    def command(name, values):
        """Capture commands with a bounded timeout and preserve every error console."""
        result = subprocess.run(values, capture_output=True, timeout=660)
        (output / (name + '.stdout')).write_bytes(result.stdout); (output / (name + '.stderr')).write_bytes(result.stderr)
        (output / (name + '.exit')).write_text(str(result.returncode)+'\n')
        if result.returncode: raise RuntimeError(name + ' failed; inspect preserved consoles')
        return result.stdout

    old = json.loads(command('model-before', [helm, 'get', 'values', 'iot-model', '-n', 'iot-llm', '-a', '-o', 'json']))
    if old['servedModel'] != 'iot-qwen2.5-1.5b' or old['image'] != lock['server_image']:
        raise ValueError('The existing backend differs; inspect its actual state instead of replacing it')
    model_dir = Path('/srv/iot-fast/models/iot-qwen2.5-7b'); model_dir.mkdir(parents=True, exist_ok=True)
    for row in lock['files']:
        target = model_dir / row['file']
        if not target.exists():
            url = f"https://huggingface.co/{lock['model_id']}/resolve/{lock['revision']}/{row['file']}"
            partial = output / (row['file'] + '.partial')
            with urlopen(url, timeout=120) as incoming, partial.open('xb') as destination:
                shutil.copyfileobj(incoming, destination, 1024 * 1024)
            if partial.stat().st_size != row['bytes'] or shared.file_digest(partial) != row['sha256']:
                raise ValueError('Candidate model shard failed its size/SHA256 pin')
            shutil.move(partial, target); target.chmod(0o644)
        if target.stat().st_size != row['bytes'] or shared.file_digest(target) != row['sha256']: raise ValueError('Preserve the differing existing shard')
    (output / 'download.json').write_text(json.dumps({'files': lock['files'], 'verified_actual_bytes': sum(row['bytes'] for row in lock['files'])}, indent=2)+'\n')
    values = dict(old, modelDirectory=str(model_dir), modelFile=lock['files'][0]['file'], servedModel=lock['served_model'],
                  resources={'requests': {'cpu': '1500m', 'memory': '4Gi'}, 'limits': {'cpu': '4', 'memory': '10Gi'}})
    values_path = output / 'candidate-values.json'; values_path.write_text(json.dumps(values, indent=2)+'\n')
    command('candidate-upgrade', [helm, 'upgrade', 'iot-model', str(source / 'infra/charts/iot-llm'), '-n', 'iot-llm', '-f', str(values_path), '--atomic', '--timeout', '10m'])
    service = json.loads(command('model-service', k + ['get', 'service', 'iot-model', '-n', 'iot-llm', '-o', 'json']))
    direct = 'http://' + service['spec']['clusterIP'] + ':8080'; shared.wait_endpoint(direct, output / 'health.json')
    selector = output / 'router-selector.json'
    selector.write_text(json.dumps({'router': {'modelServers': {'matchLabels': {'app.kubernetes.io/instance': 'iot-model'}}}}))
    command('router-upgrade', [helm, 'upgrade', 'iot-router', 'oci://ghcr.io/llm-d/charts/llm-d-router-standalone', '--version', 'v1.0.0',
                               '-n', 'iot-llm', '-f', str(source / 'infra/llm/router-values.yaml'), '-f', str(selector), '--atomic', '--timeout', '8m'])
    router = json.loads(command('router-service', k + ['get', 'service', 'iot-router-epp', '-n', 'iot-llm', '-o', 'json']))
    body = {'model': lock['served_model'], 'messages': [{'role': 'user', 'content': 'In one sentence, explain that this is a synthetic-data coursework demo.'}], 'max_tokens': 64, 'temperature': 0}
    request = Request('http://' + router['spec']['clusterIP'] + ':8081/v1/chat/completions', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=180) as response: raw = response.read()
    (output / 'response.json').write_bytes(raw)
    if json.loads(raw)['model'] != lock['served_model']: raise ValueError('The routed response identifies another model')
    command('resources-after', k + ['get', 'deployment,pod,service,inferencepool', '-n', 'iot-llm', '-o', 'json'])
    command('logs-after', k + ['logs', 'deployment/iot-model', '-n', 'iot-llm', '--tail=100'])
    report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'served_model': lock['served_model'],
              'model_file_bytes': sum(row['bytes'] for row in lock['files']),
              'scope': 'Actual candidate load and routed generation on the existing CPU VM. Grounding/tool/agent acceptance remains a separate probe.'}
    (output / 'verification.json').write_text(json.dumps(report, indent=2)+'\n'); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True); parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True); deploy(parser.parse_args())
