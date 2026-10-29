"""Verify a reserved GPU backend and controlled Helm rollback before promoting the existing llm-d route."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import ProxyHandler, build_opener, install_opener
import argparse
import json
import os
import re
import subprocess
import sys
import time


def validate_node(node, require_gpu=False):
    """Reject a foreign, unready or unreserved worker and require exactly one schedulable GPU when requested."""
    spec = node['spec']; status = node['status']; labels = node['metadata'].get('labels', {})
    if (node['metadata']['name'] != 'iot-cw-gpu' or labels.get('iot.coursework/role') != 'inference-gpu'
            or not any(c['type'] == 'Ready' and c['status'] == 'True' for c in status['conditions'])
            or not any(t.get('key') == 'iot.coursework/inference-gpu' and t.get('value') == 'true'
                       and t.get('effect') == 'NoSchedule' for t in spec.get('taints', []))
            or status['nodeInfo']['kubeletVersion'] != 'v1.35.9+k3s1'
            or status['nodeInfo']['architecture'] != 'amd64'):
        raise ValueError('Require the Ready reserved GPU worker at the verified cluster version')
    if require_gpu and status['allocatable'].get('nvidia.com/gpu') != '1':
        raise ValueError('The real device plugin must register exactly one allocatable GPU')


def validate_router(values):
    """Promote only the known CPU selector; preserve unrelated router configuration instead of replacing it."""
    if values['router']['modelServers']['matchLabels'] != {
            'app.kubernetes.io/name': 'iot-llm-model', 'app.kubernetes.io/instance': 'iot-model'}:
        raise ValueError('Preserve a different existing router backend')


def validate_weights(body, model):
    """Require the two actual Pod-mounted weight hashes and exact paths, not only a successful checksum command."""
    rows = [line.split(None, 1) for line in body.decode().splitlines() if line.strip()]
    expected = {('/models/' + member['file']): member['sha256'] for member in model['files']}
    if len(rows) != len(expected) or any(len(row) != 2 for row in rows):
        raise ValueError('The Pod checksum inventory is incomplete')
    observed = {path.strip().removeprefix('*'): pin for pin, path in rows}
    if observed != expected:
        raise ValueError('The actual Pod weight bytes differ from the pinned model')


def source_seal(root, source):
    """Require immutable project source and every native chart/operator input before Kubernetes mutations."""
    if not source.is_relative_to(root / 'data/llm-source'):
        raise ValueError('Use a fresh sealed source under the existing project')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    required = {'scripts/deploy-gpu-backend.py', 'infra/llm/gpu-device-plugin.yaml', 'infra/llm/gpu-cluster.lock.json',
        'infra/llm/gpu-runtime.lock.json', 'infra/llm/assistant.lock.json', 'infra/llm/model.lock.json',
        'infra/charts/iot-gpu-model/Chart.yaml', 'infra/charts/iot-gpu-model/values.yaml',
        'infra/charts/iot-gpu-model/templates/model.yaml', 'llm/__init__.py', 'llm/benchmark.py'}
    if not required.issubset(seal['files']):
        raise ValueError('The seal does not cover the complete GPU promotion inputs')
    for name, pin in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != pin:
            raise ValueError('Source changed: ' + name)
    return seal


def run(root, source, run_name):
    """Retain actual source, GPU, deployment and routed streams while keeping the CPU backend available for recovery."""
    root, source = root.resolve(), source.resolve()
    if os.name != 'posix' or os.geteuid() != 0 or not re.fullmatch(r'gpu-backend-[a-z0-9-]+', run_name):
        raise ValueError('Use sudo on the existing control VM and a fresh gpu-backend-* run')
    seal = source_seal(root, source)
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    if (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds() < 4500:
        raise ValueError('Require 75 minutes in the existing approved control-VM session')
    if subprocess.check_output(['systemctl', 'list-units', '--state=running', '--no-legend', 'iot-agent-chat-*']).strip():
        raise ValueError('Preserve the active native conversation before changing its backend')
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'
    os.environ['HOME'] = str(root / 'data/runtime/gpu-operator-home')
    install_opener(build_opener(ProxyHandler({})))
    sys.path.insert(0, str(source))
    from llm.benchmark import WORKLOAD, streamed_request
    k = ['/usr/local/bin/k3s', 'kubectl']; helm = str(root / 'data/runtime/kubernetes-assets-27/helm/helm')
    output = root / 'outputs' / run_name; output.mkdir(exist_ok=False)
    runtime = json.loads((source / 'infra/llm/gpu-runtime.lock.json').read_bytes())
    model = json.loads((source / 'infra/llm/assistant.lock.json').read_bytes())
    router_lock = json.loads((source / 'infra/llm/model.lock.json').read_bytes())
    route_changed = False; previous_revision = None; phase = 'preflight'

    def save(name, value):
        """Keep strict JSON results without rewriting earlier failed or measured runs."""
        (output / name).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')

    def command(label, args, timeout=600, check=True):
        """Record the exact exit and original command streams; no Secret object or credential argument is queried."""
        result = subprocess.run(args, cwd=source, capture_output=True, timeout=timeout)
        (output / (label + '.stdout')).write_bytes(result.stdout)
        (output / (label + '.stderr')).write_bytes(result.stderr)
        (output / (label + '.exit')).write_text(str(result.returncode) + '\n')
        if check and result.returncode:
            raise RuntimeError(label + ' failed; retain original receipts')
        return result

    def get(label, args):
        """Decode only a successfully recorded native JSON readback."""
        return json.loads(command(label, args).stdout)

    def smoke(label, url):
        """Keep three real streamed generations and require the candidate image fingerprint and model identity."""
        rows = []
        for prompt in WORKLOAD:
            body = {'model': model['served_model'], 'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0, 'seed': 7, 'max_tokens': 64, 'cache_prompt': False,
                'stream': True, 'stream_options': {'include_usage': True}}
            row = streamed_request(url, body); rows.append(row); save(label + '.json', rows)
            if ({event.get('system_fingerprint') for event in row['events']} != {'b11977-7ac5727d6'}
                    or row['usage']['prompt_tokens_details']['cached_tokens'] != 0):
                raise ValueError('Routed/direct generation does not identify the verified CUDA candidate')
        return rows

    save('source.json', seal)
    try:
        node = get('node-before', k + ['get', 'node', 'iot-cw-gpu', '-o', 'json']); validate_node(node)
        get('nodes-before', k + ['get', 'nodes', '-o', 'json'])
        get('pods-before', k + ['get', 'pods', '-A', '-o', 'json'])
        before = get('router-values-before', [helm, 'get', 'values', 'iot-router', '-n', 'iot-llm', '-a', '-o', 'json'])
        validate_router(before)
        history = get('router-history-before', [helm, 'history', 'iot-router', '-n', 'iot-llm', '-o', 'json'])
        previous_revision = history[-1]['revision']
        get('model-config-before', k + ['get', 'modelconfig', 'default-model-config', '-n', 'iot-agents', '-o', 'json'])
        phase = 'device-plugin'
        plugin = str(source / 'infra/llm/gpu-device-plugin.yaml')
        command('plugin-server-validation', k + ['apply', '--dry-run=server', '-f', plugin])
        command('plugin-apply', k + ['apply', '--server-side', '--field-manager=iot-gpu-inference', '-f', plugin])
        command('plugin-ready', k + ['rollout', 'status', 'daemonset/iot-nvidia-device-plugin', '-n', 'kube-system', '--timeout=300s'], timeout=330)
        expires = time.monotonic() + 90; attempt = 0
        while True:
            attempt += 1
            node = get('node-allocation-' + str(attempt), k + ['get', 'node', 'iot-cw-gpu', '-o', 'json'])
            try: validate_node(node, require_gpu=True); break
            except ValueError:
                if time.monotonic() >= expires: raise
                time.sleep(3)
        phase = 'candidate'
        chart = str(source / 'infra/charts/iot-gpu-model')
        command('chart-lint', [helm, 'lint', chart])
        command('candidate-install', [helm, 'upgrade', '--install', 'iot-model-gpu', chart, '-n', 'iot-llm',
            '--atomic', '--wait', '--timeout=10m'], timeout=660)
        service = get('candidate-service', k + ['get', 'service', 'iot-model-gpu', '-n', 'iot-llm', '-o', 'json'])
        direct = 'http://' + service['spec']['clusterIP'] + ':8080'
        device = command('candidate-gpu', k + ['exec', 'deployment/iot-model-gpu', '-n', 'iot-llm', '--',
            'nvidia-smi', '--query-gpu=name,driver_version,memory.total', '--format=csv,noheader']).stdout.decode().strip()
        if device != 'Tesla V100-SXM2-16GB, 580.178.04, 16384 MiB':
            raise ValueError('The actual Pod does not report the expected device and driver')
        weights = command('candidate-weights', k + ['exec', 'deployment/iot-model-gpu', '-n', 'iot-llm', '--',
            'sha256sum'] + ['/models/' + item['file'] for item in model['files']]).stdout
        validate_weights(weights, model)
        smoke('direct', direct)
        phase = 'controlled-fallback'
        bad = command('candidate-bad-image', [helm, 'upgrade', 'iot-model-gpu', chart, '-n', 'iot-llm',
            '--set', 'image=iot-unavailable-gpu:missing', '--set', 'imagePullPolicy=Never',
            '--atomic', '--wait', '--timeout=90s'], timeout=240, check=False)
        if bad.returncode == 0:
            raise ValueError('The deliberately missing image unexpectedly deployed')
        command('candidate-events-after-rollback', k + ['get', 'events', '-n', 'iot-llm', '-o', 'json'])
        history = get('candidate-history', [helm, 'history', 'iot-model-gpu', '-n', 'iot-llm', '-o', 'json'])
        if history[-1]['status'] != 'deployed' or not history[-1]['description'].startswith('Rollback to '):
            raise ValueError('The controlled bad image did not automatically recover')
        actual = get('candidate-values', [helm, 'get', 'values', 'iot-model-gpu', '-n', 'iot-llm', '-a', '-o', 'json'])
        if actual['image'] != runtime['image']:
            raise ValueError('The restored GPU image differs from the reviewed candidate')
        smoke('after-rollback', direct)
        phase = 'router-promotion'
        after = json.loads(json.dumps(before))
        after['router']['modelServers']['matchLabels'] = {
            'app.kubernetes.io/name': 'iot-llm-model-gpu', 'app.kubernetes.io/instance': 'iot-model-gpu'}
        values_path = output / 'router-values.json'; save('router-values.json', after)
        route_changed = True
        command('router-upgrade', [helm, 'upgrade', 'iot-router', router_lock['router_chart'], '--version', router_lock['router_version'],
            '-n', 'iot-llm', '-f', str(values_path), '--atomic', '--wait', '--timeout=5m'], timeout=330)
        command('inference-pool', k + ['get', 'inferencepool', '-n', 'iot-llm', '-o', 'json'])
        router_service = get('router-service', k + ['get', 'service', 'iot-router-epp', '-n', 'iot-llm', '-o', 'json'])
        routed = 'http://' + router_service['spec']['clusterIP'] + ':8081'
        smoke('routed', routed)
        get('resources-after', k + ['get', 'deployment,service,pod,inferencepool', '-n', 'iot-llm', '-o', 'json'])
        get('pods-after', k + ['get', 'pods', '-A', '-o', 'json'])
        command('gpu-model-logs', k + ['logs', 'deployment/iot-model-gpu', '-n', 'iot-llm', '--tail=160'])
        command('router-values-after', [helm, 'get', 'values', 'iot-router', '-n', 'iot-llm', '-a', '-o', 'json'])
        command('model-config-after', k + ['get', 'modelconfig', 'default-model-config', '-n', 'iot-agents', '-o', 'json'])
        report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'gpu_node': node['metadata']['uid'],
            'image': runtime['image'], 'routed_gpu_verified': True, 'controlled_atomic_rollbacks': 1,
            'actual_generation_requests': 9, 'cloud_resources_created': 0, 'agent_chat_verified': False,
            'coursework_complete': False, 'scope': 'Actual reserved GPU Pod and llm-d route with three direct, three after-rollback and three routed streams. Specialist/coordinator acceptance remains separate.'}
    except Exception as error:
        restore = None
        if route_changed:
            restore = command('router-emergency-rollback', [helm, 'rollback', 'iot-router', str(previous_revision), '-n', 'iot-llm',
                '--wait', '--timeout=3m'], timeout=210, check=False).returncode
        command('failure-resources', k + ['get', 'deployment,pod,inferencepool', '-n', 'iot-llm', '-o', 'json'], check=False)
        command('failure-events', k + ['get', 'events', '-A', '-o', 'json'], check=False)
        report = {'passed': False, 'phase': phase, 'error_type': type(error).__name__, 'error': str(error),
            'router_emergency_rollback_exit': restore, 'agent_chat_verified': False, 'coursework_complete': False}
        save('verification.json', report); raise
    save('verification.json', report); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--run', required=True)
    args = parser.parse_args(); run(args.runtime_root, args.source_root, args.run)
