"""Build a source-sealed internal tool-turn adapter and promote it only after real GPU generation checks."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import Request, ProxyHandler, build_opener
import argparse
import json
import os
import re
import subprocess
import tarfile


def run(root, source, run_name):
    """Preserve native build, routing and model replies; acceptance of real agent conversations is separate."""
    root, source = root.resolve(), source.resolve()
    if os.name != 'posix' or os.geteuid() != 0 or not re.fullmatch(r'model-control-[a-z0-9-]+', run_name):
        raise ValueError('Use sudo and a fresh model-control-* run on the retained VM')
    if not source.is_relative_to(root / 'data/llm-source'):
        raise ValueError('Use source sealed inside the existing project')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    required = {'tools/model-control/go.mod', 'tools/model-control/main.go', 'tools/model-control/main_test.go',
        'tools/model-control/Dockerfile', 'infra/charts/iot-model-control/Chart.yaml',
        'infra/charts/iot-model-control/values.yaml', 'infra/charts/iot-model-control/templates/model.yaml',
        'scripts/deploy-model-control.py', 'infra/llm/model.lock.json', 'infra/llm/assistant.lock.json'}
    if not required.issubset(seal['files']):
        raise ValueError('Require all source, chart and model identities')
    for name, pin in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != pin:
            raise ValueError('The sealed source differs: ' + name)
    binary = source.parent / 'iot-model-control-linux-amd64'
    if sha256(binary.read_bytes()).hexdigest() != seal['binary_sha256'] or binary.read_bytes()[:4] != b'\x7fELF':
        raise ValueError('The tested static Linux binary differs')
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    if (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds() < 2400:
        raise ValueError('Require forty authorized minutes in the existing session')
    if subprocess.check_output(['systemctl', 'list-units', '--state=running', '--no-legend', 'iot-agent-chat-*']).strip():
        raise ValueError('Settle the active conversation before routing changes')
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'
    output = root / 'outputs' / run_name; output.mkdir(exist_ok=False)
    k = ['/usr/local/bin/k3s', 'kubectl']
    helm = str(root / 'data/runtime/kubernetes-assets-27/helm/helm')
    opener = build_opener(ProxyHandler({}))
    phase = 'preflight'; promoted = False; prior_revision = None

    def save(name, value):
        """Retain a JSON receipt without changing original HTTP response payloads."""
        (output / name).write_text(json.dumps(value, indent=2) + '\n')

    def command(label, argv, timeout=90, check=True):
        """Record each bounded native command independently, including failed rollback trials."""
        result = subprocess.run(argv, capture_output=True, timeout=timeout)
        save(label + '.command.json', argv)
        (output / (label + '.stdout')).write_bytes(result.stdout)
        (output / (label + '.stderr')).write_bytes(result.stderr)
        (output / (label + '.exit')).write_text(str(result.returncode) + '\n')
        if check and result.returncode: raise RuntimeError(label + ' failed')
        return result

    def get(label, argv):
        """Decode a successfully retained JSON result."""
        return json.loads(command(label, argv).stdout)

    def selection(label, base):
        """Verify three actual uncached selections from an unmodified input that contains no stop policy."""
        body = json.loads((root / 'outputs/model-serial-pilot-29a/request.json').read_bytes())
        if 'stop' in body: raise ValueError('The pilot input must not contain the adapter policy')
        rows = []
        for number in range(3):
            save(label + '-' + str(number) + '-request.json', body)
            with opener.open(Request(base + '/v1/chat/completions', data=json.dumps(body).encode(),
                    headers={'Content-Type': 'application/json'}), timeout=180) as response:
                raw = response.read(); headers = dict(response.headers); status = response.status
                control = response.headers.get('X-Iot-Tool-Control')
            (output / (label + '-' + str(number) + '-response.json')).write_bytes(raw)
            value = json.loads(raw); choice = value['choices'][0]; calls = choice['message'].get('tool_calls', [])
            if (status != 200 or control != 'serial-close-v1'
                    or value.get('system_fingerprint') != 'b11977-7ac5727d6' or choice['finish_reason'] != 'tool_calls'
                    or len(calls) != 1 or not isinstance(json.loads(calls[0]['function']['arguments']), dict)
                    or value['usage']['prompt_tokens_details']['cached_tokens'] != 0):
                raise ValueError('The actual GPU selection did not terminate one complete uncached call')
            rows.append({'status': status, 'headers': headers, 'call_count': len(calls), 'usage': value['usage']})
        save(label + '-verification.json', rows)

    save('source.json', seal)
    try:
        before = get('router-values-before', [helm, 'get', 'values', 'iot-router', '-n', 'iot-llm', '-a', '-o', 'json'])
        prior_selector = before['router']['modelServers']['matchLabels']
        if prior_selector == {'app.kubernetes.io/name': 'iot-llm-model-control', 'app.kubernetes.io/instance': 'iot-model-control'}:
            prior = get('adapter-values-before', [helm, 'get', 'values', 'iot-model-control', '-n', 'iot-llm', '-a', '-o', 'json'])
            if prior['image'] not in {'docker.io/library/iot-model-control@sha256:681c6fdd5df63911248bc5a4f3be53a61cd193b8ae59463b0f4a09e5f5a7de10',
                    'docker.io/library/iot-model-control@sha256:1da710e876ac93d789f27e49bcd360f563ee55b64fc5d732659bb9d6883322e8',
                    'docker.io/library/iot-model-control@sha256:f24a9aa492b9219bb9c73ae60d17973c4486f15393491969abc65b02883dc428'}:
                raise ValueError('Preserve a different adapter release')
        elif prior_selector != {'app.kubernetes.io/name': 'iot-llm-model-gpu', 'app.kubernetes.io/instance': 'iot-model-gpu'}:
            raise ValueError('Preserve a different existing model route')
        history = get('router-history-before', [helm, 'history', 'iot-router', '-n', 'iot-llm', '-o', 'json'])
        prior_revision = history[-1]['revision']
        get('model-config-before', k + ['get', 'modelconfig', 'default-model-config', '-n', 'iot-agents', '-o', 'json'])
        phase = 'native-image'
        context = output / 'build'; context.mkdir()
        (context / 'iot-model-control-linux-amd64').write_bytes(binary.read_bytes())
        (context / 'iot-model-control-linux-amd64').chmod(0o755)
        (context / 'Dockerfile').write_bytes((source / 'tools/model-control/Dockerfile').read_bytes())
        tag = 'iot-model-control:' + seal['binary_sha256'][:16]
        command('image-build', ['docker', 'build', '--network=none', '-t', tag, str(context)], timeout=180)
        image = get('image-inspect', ['docker', 'image', 'inspect', tag])[0]
        if image['Architecture'] != 'amd64' or image['Os'] != 'linux' or image['Config']['User'] != '10001:10001':
            raise ValueError('The native adapter image identity differs')
        archive = output / 'image.tar'
        command('image-export', ['docker', 'save', '-o', str(archive), tag], timeout=120)
        with tarfile.open(archive) as package:
            index = json.load(package.extractfile('index.json'))
            descriptors = [v for v in index['manifests'] if v.get('annotations', {}).get('io.containerd.image.name') == 'docker.io/library/' + tag]
            if len(descriptors) != 1: raise ValueError('Require one exported OCI image descriptor')
            digest = descriptors[0]['digest']
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest): raise ValueError('Invalid image descriptor')
            if sha256(package.extractfile('blobs/sha256/' + digest.split(':')[1]).read()).hexdigest() != digest.split(':')[1]:
                raise ValueError('The exported native OCI index content differs')
        image_ref = 'docker.io/library/iot-model-control@' + digest
        command('image-import', ['/usr/local/bin/k3s', 'ctr', 'images', 'import', '--digests', str(archive)], timeout=120)
        command('image-digest-reference', ['/usr/local/bin/k3s', 'ctr', 'images', 'tag', 'docker.io/library/' + tag, image_ref])
        imported = get('image-cri', ['/usr/local/bin/k3s', 'crictl', 'inspecti', image_ref])['status']
        refs = [v for v in imported['repoDigests'] if v.startswith('docker.io/library/iot-model-control@sha256:')]
        if refs != [image_ref]: raise ValueError('Require the exact native imported OCI index digest')
        values = output / 'values.json'; save('values.json', {'image': refs[0]})
        phase = 'candidate'
        chart = str(source / 'infra/charts/iot-model-control')
        command('adapter-upgrade', [helm, 'upgrade', '--install', 'iot-model-control', chart, '-n', 'iot-llm',
            '-f', str(values), '--atomic', '--wait', '--timeout=4m'], timeout=270)
        get('candidate-resources', k + ['get', 'deployment,pod,service', '-n', 'iot-llm', '-o', 'json'])
        svc = get('candidate-service', k + ['get', 'service', 'iot-model-control', '-n', 'iot-llm', '-o', 'json'])
        selection('direct', 'http://' + svc['spec']['clusterIP'] + ':8080')
        phase = 'route-promotion'
        routed_values = json.loads(json.dumps(before))
        routed_values['router']['modelServers']['matchLabels'] = {
            'app.kubernetes.io/name': 'iot-llm-model-control', 'app.kubernetes.io/instance': 'iot-model-control'}
        route = output / 'router-values.json'; save('router-values.json', routed_values)
        lock = json.loads((source / 'infra/llm/model.lock.json').read_bytes()); promoted = True
        command('router-upgrade', [helm, 'upgrade', 'iot-router', lock['router_chart'], '--version', lock['router_version'],
            '-n', 'iot-llm', '-f', str(route), '--atomic', '--wait', '--timeout=4m'], timeout=270)
        router = get('router-service', k + ['get', 'service', 'iot-router-epp', '-n', 'iot-llm', '-o', 'json'])
        selection('routed', 'http://' + router['spec']['clusterIP'] + ':8081')
        get('model-config-after', k + ['get', 'modelconfig', 'default-model-config', '-n', 'iot-agents', '-o', 'json'])
        get('router-values-after', [helm, 'get', 'values', 'iot-router', '-n', 'iot-llm', '-a', '-o', 'json'])
        report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'image': refs[0],
            'gpu_selection_requests': 6, 'generated_calls_per_request': 1, 'agent_chat_verified': False,
            'response_rewriting': False, 'coursework_complete': False}
    except Exception as error:
        restored = None
        if promoted:
            restored = command('router-emergency-rollback', [helm, 'rollback', 'iot-router', str(prior_revision),
                '-n', 'iot-llm', '--wait', '--timeout=3m'], timeout=210, check=False).returncode
        report = {'passed': False, 'phase': phase, 'error_type': type(error).__name__, 'error': str(error),
            'router_emergency_rollback_exit': restored, 'agent_chat_verified': False, 'coursework_complete': False}
        save('verification.json', report); raise
    save('verification.json', report); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--run', required=True)
    args = parser.parse_args(); run(args.runtime_root, args.source_root, args.run)
