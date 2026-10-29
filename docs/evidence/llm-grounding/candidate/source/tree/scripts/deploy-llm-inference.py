"""Install and verify the private llm-d router and a pinned CPU model on the retained coursework VM."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import Request, urlopen
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time


def file_digest(path):
    """Hash retained/downloaded model bytes without loading the complete model into Python memory."""
    digest = sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()


def resource_gate(runtime_root, deadline):
    """Require the two existing data mounts, memory/disk reserves and enough of the approved VM session."""
    if os.name != 'posix' or os.geteuid() != 0: raise ValueError('Run on the existing Linux VM with sudo')
    if not runtime_root.is_relative_to('/srv/iot-data') or not runtime_root.is_dir(): raise ValueError('Use the retained project directory')
    if not all(Path(p).is_mount() for p in ['/srv/iot-data', '/srv/iot-fast']): raise ValueError('Existing data and SSD mounts required')
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
    memory = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))) * 1024
    disk = shutil.disk_usage('/srv/iot-fast').free
    if remaining < 3600 or memory < 12 * 1024**3 or disk < 30 * 1024**3:
        raise ValueError('Require one hour remaining, 12 GiB available RAM and 30 GiB free SSD')
    return {'remaining_session_seconds': remaining, 'available_memory_bytes': memory, 'ssd_free_bytes': disk}


def wait_endpoint(base_url, output):
    """Wait for the actual Service data path after rollout, preserving every health attempt."""
    attempts = []; deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urlopen(base_url + '/health', timeout=5) as response:
                value = json.load(response)
                attempts.append({'status': response.status, 'body': value})
                if response.status == 200 and value.get('status') == 'ok':
                    Path(output).write_text(json.dumps(attempts, indent=2)+'\n'); return
        except OSError as error:
            attempts.append({'error': type(error).__name__, 'detail': str(error)})
        time.sleep(2)
    Path(output).write_text(json.dumps(attempts, indent=2)+'\n')
    raise RuntimeError('The model Service data path did not become healthy')


def deploy(args):
    """Preserve all older workloads and outputs while installing only the declared iot-llm releases."""
    root = args.runtime_root.resolve(); source = args.source_root.resolve(); output = args.output.resolve()
    if not source.is_relative_to(root / 'data') or not output.is_relative_to(root / 'outputs'):
        raise ValueError('Keep a sealed source under data/ and a new report under outputs/')
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    deadline = datetime.fromisoformat(session['auto_stop_utc'])
    budget = resource_gate(root, deadline)
    if output.exists(): raise ValueError('Choose a fresh output directory; failed evidence is retained')
    output.mkdir(parents=True)
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    for name, digest in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or file_digest(path) != digest: raise ValueError('Source seal mismatch: ' + name)
    (output / 'source.json').write_text(json.dumps(seal, indent=2)+'\n')
    lock = json.loads((source / 'infra/llm/model.lock.json').read_bytes())
    (output / 'model-lock.json').write_text(json.dumps(lock, indent=2)+'\n')
    (output / 'budget.json').write_text(json.dumps(budget, indent=2)+'\n')
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'
    k = ['/usr/local/bin/k3s', 'kubectl']; helm = str(root / 'data/runtime/kubernetes-assets-27/helm/helm')

    def command(label, values, timeout=600, check=True):
        """Preserve each actual bounded command's stdout/stderr and return code without printing secrets."""
        result = subprocess.run(values, cwd=source, capture_output=True, timeout=timeout)
        (output / (label + '.stdout')).write_bytes(result.stdout); (output / (label + '.stderr')).write_bytes(result.stderr)
        (output / (label + '.exit')).write_text(str(result.returncode)+'\n')
        if check and result.returncode: raise RuntimeError(label + ' failed; inspect the preserved console')
        return result

    def read_json(label, values):
        """Decode an original command response only after its successful exit was recorded."""
        return json.loads(command(label, values).stdout)

    try:
        namespace = command('namespace-before', k + ['get', 'namespace', 'iot-llm', '-o', 'json'], check=False)
        if (namespace.returncode == 0) != bool(args.resume_baseline):
            raise ValueError('Use initial installation only for an absent namespace, or explicitly resume a verified baseline')
        nodes = read_json('nodes-before', k + ['get', 'nodes', '-o', 'json'])
        if len(nodes['items']) != 1: raise ValueError('This resource budget is for the retained single-node lab')
        command('node-budget', k + ['describe', 'node', nodes['items'][0]['metadata']['name']])
        command('pods-before', k + ['get', 'pods', '-A', '-o', 'json'])
        model_dir = Path('/srv/iot-fast/models/iot-qwen2.5-1.5b'); model_dir.mkdir(parents=True, exist_ok=True)
        model = model_dir / lock['file']
        if not model.exists():
            url = f"https://huggingface.co/{lock['model_id']}/resolve/{lock['revision']}/{lock['file']}"
            partial = output / (lock['file'] + '.partial')
            started = time.monotonic()
            with urlopen(url, timeout=120) as incoming, partial.open('xb') as target:
                shutil.copyfileobj(incoming, target, length=1024 * 1024)
            if partial.stat().st_size != lock['bytes'] or file_digest(partial) != lock['sha256']:
                raise ValueError('Downloaded model bytes failed the pinned size/SHA256')
            shutil.move(partial, model); model.chmod(0o644)
            (output / 'download.json').write_text(json.dumps({'url': url, 'seconds': time.monotonic()-started, 'bytes': model.stat().st_size, 'sha256': file_digest(model)}, indent=2)+'\n')
        if model.stat().st_size != lock['bytes'] or file_digest(model) != lock['sha256']:
            raise ValueError('Existing model file differs; preserve it and investigate')
        if args.resume_baseline:
            prior = args.resume_baseline.resolve()
            if not prior.is_relative_to(root / 'outputs') or not (prior / 'failure.json').is_file():
                raise ValueError('Resume only a retained failed initial run with a completed baseline')
            old_lock = json.loads((prior / 'model-lock.json').read_bytes())
            old_nodes = json.loads((prior / 'nodes-before.stdout').read_bytes())
            old_summary = json.loads((prior / 'baseline/summary.json').read_bytes())
            if old_lock != lock or old_nodes['items'][0]['metadata']['uid'] != nodes['items'][0]['metadata']['uid'] or old_summary['requests'] != 9:
                raise ValueError('Baseline model, machine or workload differs')
            old_values = read_json('resume-model-values', [helm, 'get', 'values', 'iot-model', '-n', 'iot-llm', '-a', '-o', 'json'])
            if old_values['image'] != lock['server_image'] or old_values['servedModel'] != lock['served_model'] or old_values['threads'] != 4:
                raise ValueError('The retained optimized backend is not the expected model/configuration')
            shutil.copytree(prior / 'baseline', output / 'baseline')
            (output / 'baseline-origin.json').write_text(json.dumps({'path': str(prior), 'source_seal_sha256': file_digest(prior / 'source.json'),
                'requests_sha256': file_digest(prior / 'baseline/requests.json'), 'scope': 'Original completed baseline retained unchanged; not remeasured during recovery.'}, indent=2)+'\n')
        else:
            command('create-namespace', k + ['create', 'namespace', 'iot-llm'])
        crd_url = f"https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/{lock['inference_extension_version']}/v1-manifests.yaml"
        crd_file = output / 'inference-extension.yaml'
        crd_file.write_bytes(urlopen(crd_url, timeout=60).read())
        before = command('inference-pool-before', k + ['get', 'crd', 'inferencepools.inference.networking.k8s.io', '-o', 'json'], check=False)
        if before.returncode == 0 and not args.resume_baseline: raise ValueError('An inference CRD already exists; review compatibility before changing shared CRDs')
        if before.returncode != 0:
            command('install-inference-crd', k + ['apply', '--server-side', '-f', str(crd_file)])
        command('wait-inference-crd', k + ['wait', '--for=condition=Established', '--timeout=60s', 'crd/inferencepools.inference.networking.k8s.io'])
        chart = source / 'infra/charts/iot-llm'
        command('model-chart-lint', [helm, 'lint', str(chart)])
        if not args.resume_baseline:
            command('model-baseline-install', [helm, 'upgrade', '--install', 'iot-model', str(chart), '-n', 'iot-llm', '--set', 'threads=1', '--atomic', '--timeout', '10m'], timeout=660)
        model_service = read_json('model-service', k + ['get', 'service', 'iot-model', '-n', 'iot-llm', '-o', 'json'])
        direct = 'http://' + model_service['spec']['clusterIP'] + ':8080'
        sys.path.insert(0, str(source))
        from llm.benchmark import benchmark
        if not args.resume_baseline:
            wait_endpoint(direct, output / 'baseline-health.json')
            benchmark(direct, lock['served_model'], output / 'baseline', repeats=3)
        command('model-optimized-install', [helm, 'upgrade', 'iot-model', str(chart), '-n', 'iot-llm', '--set', 'threads=4', '--atomic', '--timeout', '10m'], timeout=660)
        wait_endpoint(direct, output / 'optimized-health.json')
        benchmark(direct, lock['served_model'], output / 'optimized', repeats=3)
        command('router-chart', [helm, 'show', 'chart', lock['router_chart'], '--version', lock['router_version']])
        command('router-install', [helm, 'upgrade', '--install', 'iot-router', lock['router_chart'], '--version', lock['router_version'],
                                  '-n', 'iot-llm', '-f', str(source / 'infra/llm/router-values.yaml'), '--atomic', '--timeout', '8m'], timeout=540)
        router = read_json('router-service', k + ['get', 'service', 'iot-router-epp', '-n', 'iot-llm', '-o', 'json'])
        routed = 'http://' + router['spec']['clusterIP'] + ':8081'
        benchmark(routed, lock['served_model'], output / 'routed', repeats=1)
        tool_body = {'model': lock['served_model'], 'messages': [{'role': 'user', 'content': 'Call get_machine_features for machine P-001. Do not answer in prose.'}],
                     'max_tokens': 128, 'temperature': 0, 'tools': [{'type': 'function', 'function': {'name': 'get_machine_features', 'description': 'Read current pump features.',
                     'parameters': {'type': 'object', 'properties': {'machine_id': {'type': 'string'}}, 'required': ['machine_id']}}}]}
        req = Request(routed + '/v1/chat/completions', data=json.dumps(tool_body).encode(), headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=180) as response: tool_response = response.read()
        (output / 'tool-response.json').write_bytes(tool_response)
        parsed = json.loads(tool_response); call = parsed['choices'][0]['message']['tool_calls'][0]['function']
        if call['name'] != 'get_machine_features' or json.loads(call['arguments']) != {'machine_id': 'P-001'}:
            raise ValueError('Model did not produce the required genuine structured tool call')
        command('model-logs', k + ['logs', 'deployment/iot-model', '-n', 'iot-llm', '--tail=150'])
        command('router-logs', k + ['logs', 'deployment/iot-router-epp', '-n', 'iot-llm', '--all-containers', '--tail=100'])
        command('resources-after', k + ['get', 'deployment,service,pod,inferencepool', '-n', 'iot-llm', '-o', 'json'])
        command('replicasets-after', k + ['get', 'replicaset', '-n', 'iot-llm', '-o', 'json'])
        command('top-after', k + ['top', 'pods', '-n', 'iot-llm'])
        command('helm-model', [helm, 'get', 'values', 'iot-model', '-n', 'iot-llm', '-a', '-o', 'json'])
        command('helm-router', [helm, 'get', 'values', 'iot-router', '-n', 'iot-llm', '-a', '-o', 'json'])
        report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_base_commit': seal['base_commit'],
                  'model_sha256': lock['sha256'], 'router_version': lock['router_version'], 'model_file_bytes': lock['bytes'],
                  'namespace': 'iot-llm', 'actual_structured_tool_call': True, 'baseline_threads': 1, 'optimized_threads': 4,
                  'scope': 'Real CPU model inference and llm-d routing, isolated namespace on existing VM. Not RAG, MCP tool execution, agents, autoscaling or HA proof.'}
        (output / 'verification.json').write_text(json.dumps(report, indent=2)+'\n'); print(json.dumps(report, indent=2))
    except Exception as error:
        (output / 'failure.json').write_text(json.dumps({'type': type(error).__name__, 'message': str(error), 'checked_utc': datetime.now(timezone.utc).isoformat()}, indent=2)+'\n')
        for name, values in [('failure-pods', ['get', 'pods', '-n', 'iot-llm', '-o', 'json']), ('failure-events', ['get', 'events', '-n', 'iot-llm', '-o', 'json'])]:
            command(name, k + values, check=False)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True); parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume-baseline', type=Path, help='Explicitly retain the completed baseline from a failed initial run; never overwrite its outputs')
    deploy(parser.parse_args())
