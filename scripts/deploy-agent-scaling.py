"""Install pinned KEDA and role-specific scaling against the existing Substrate worker pools."""
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
import time
import yaml

SOURCE_FILES = {'scripts/deploy-agent-scaling.py', 'infra/agents/keda-values.yaml', 'infra/agents/keda.lock.json',
                'infra/charts/iot-agent-scaling/Chart.yaml', 'infra/charts/iot-agent-scaling/values.yaml',
                'infra/charts/iot-agent-scaling/templates/scaledobjects.yaml', 'infra/charts/iot-agent-scaling/templates/network.yaml'}
ROLES = {'retrieval', 'drift', 'coordinator'}


def validate_scalers(documents):
    """Require private role-specific demand signals, bounded replicas and conservative scale-in."""
    documents = [d for d in documents if d is not None and d.get('kind') == 'ScaledObject']
    if len(documents) != 3 or {d['metadata']['name'] for d in documents} != {'iot-' + r + '-workers' for r in ROLES}:
        raise ValueError('Require exactly three owned scalers')
    for doc in documents:
        role = doc['metadata']['name'].removeprefix('iot-').removesuffix('-workers'); spec = doc['spec']
        expected_path = 'data.actors.#(status=="Running")#|#(atespace=="iot-agents")#|#(actorTemplateName%"iot-' + role + '-*")#|#'
        if (doc['kind'] != 'ScaledObject' or doc['metadata']['labels'].get('iot.coursework/agent-scaling') != 'true'
                or spec['scaleTargetRef'] != {'apiVersion': 'ate.dev/v1alpha1', 'kind': 'WorkerPool', 'name': 'iot-' + role + '-pool'}
                or spec['minReplicaCount'] != 2 or spec['maxReplicaCount'] != 4 or spec['pollingInterval'] != 5
                or spec['fallback'] != {'failureThreshold': 3, 'replicas': 4, 'behavior': 'currentReplicasIfHigher'}):
            raise ValueError('Preserve owned targets, warm capacity and the reviewed maximum')
        expected_trigger = {'type': 'metrics-api', 'name': 'busyActors', 'metricType': 'AverageValue', 'metadata': {
            'url': 'http://kagent-controller.iot-agents.svc.cluster.local:8083/api/substrate/status',
            'format': 'json', 'valueLocation': expected_path, 'targetValue': '0.5', 'activationTargetValue': '0', 'timeout': '5s'}}
        if spec['triggers'] != [expected_trigger]: raise ValueError('Reject unrelated actors, fabricated metrics or external endpoints')
        if spec['advanced']['horizontalPodAutoscalerConfig']['behavior']['scaleDown'] != {
                'stabilizationWindowSeconds': 180, 'policies': [{'type': 'Pods', 'value': 1, 'periodSeconds': 30}]}:
            raise ValueError('Preserve the scale-in observation window and bounded rate')
    return True


def selector_patch(crd):
    """Add the missing standard scale selector to the pinned single-version WorkerPool CRD only."""
    versions = crd['spec']['versions']
    if crd['metadata']['name'] != 'workerpools.ate.dev' or len(versions) != 1 or versions[0]['name'] != 'v1alpha1':
        raise ValueError('Review a different WorkerPool CRD before compatibility changes')
    version = versions[0]; scale = version['subresources']['scale']
    if (scale.get('specReplicasPath') != '.spec.replicas' or scale.get('statusReplicasPath') != '.status.replicas'
            or scale.get('labelSelectorPath', '.spec.selector') not in {'.status.selector', '.spec.selector'}):
        raise ValueError('Preserve an unrelated scale schema')
    properties = version['schema']['openAPIV3Schema']['properties']['spec']['properties']
    if properties.get('selector', {'type': 'string'}) != {'type': 'string'}: raise ValueError('Preserve a different selector schema')
    return [{'op': 'test', 'path': '/metadata/resourceVersion', 'value': crd['metadata']['resourceVersion']},
            {'op': 'add', 'path': '/spec/versions/0/schema/openAPIV3Schema/properties/spec/properties/selector', 'value': {'type': 'string'}},
            {'op': 'add', 'path': '/spec/versions/0/subresources/scale/labelSelectorPath', 'value': '.spec.selector'}]


def run(root, source, run_name, repair_existing=False):
    """Guard source/resources/idle state and record individually atomic platform and scaler installations."""
    root, source = root.resolve(), source.resolve()
    if os.geteuid() != 0 or not source.is_relative_to(root / 'data/llm-source') or not re.fullmatch(r'agent-scaling-[a-z0-9-]+', run_name):
        raise ValueError('Require sudo, a sealed project source and a fresh agent-scaling run')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    if set(seal['files']) != SOURCE_FILES: raise ValueError('Seal every chart, lock, value and operator input')
    for name, digest in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != digest: raise ValueError('Source changed: ' + name)
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    remaining = (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds()
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))) * 1024
    free = shutil.disk_usage('/srv/iot-fast').free
    if remaining < 5400 or available < 22 * 1024**3 or free < 25 * 1024**3:
        raise ValueError('Require ninety session minutes, 22 GiB available RAM and 25 GiB SSD')
    output = root / 'outputs' / run_name; output.mkdir(exist_ok=False)
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'; private = []
    for name in ['agent-platform.json', 'mcp-tools.json']:
        for value in json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values(): private.extend([value.encode(), base64.b64encode(value.encode())])

    def command(name, args, payload=None):
        """Retain exact command receipts after checking actual credentials, with bounded operation time."""
        result = subprocess.run(args, input=payload, capture_output=True, timeout=480)
        if any(value in result.stdout or value in result.stderr for value in private): raise ValueError('Credential-bearing receipt refused')
        for tail, raw in [('stdout', result.stdout), ('stderr', result.stderr), ('exit', str(result.returncode).encode() + b'\n')]: (output / (name + '.' + tail)).write_bytes(raw)
        if result.returncode: raise RuntimeError(name + ' failed; retain original receipts')
        return result.stdout

    (output / 'source.json').write_text(json.dumps(seal, indent=2) + '\n')
    (output / 'resources.json').write_text(json.dumps({'remaining_seconds': remaining, 'available_ram_bytes': available, 'ssd_free_bytes': free, 'new_cloud_resources': False}, indent=2) + '\n')
    k = ['/usr/local/bin/k3s', 'kubectl']; helm = str(root / 'data/runtime/kubernetes-assets-27/helm/helm'); phase = 'preflight'
    try:
        releases = json.loads(command('releases-before', [helm, 'list', '-A', '-a', '-o', 'json']))
        existing = [r for r in releases if r['name'] in {'iot-agent-keda', 'iot-agent-scaling'} or r['chart'].startswith('keda-')]
        if repair_existing:
            if ({r['name'] for r in existing} != {'iot-agent-keda', 'iot-agent-scaling'}
                    or any(r['status'] != 'deployed' for r in existing)
                    or next(r for r in existing if r['name'] == 'iot-agent-keda')['chart'] != 'keda-2.21.0'):
                raise ValueError('Repair only the two observed owned, deployed releases')
        elif existing: raise ValueError('Preserve any existing KEDA or scaling release')
        nodes = json.loads(command('nodes', k + ['get', 'nodes', '-o', 'json']))['items']
        if not all(n['status']['nodeInfo']['kubeletVersion'].startswith('v1.35.') for n in nodes): raise ValueError('Review a different Kubernetes version')
        pools = json.loads(command('pools-before', k + ['get', 'workerpools', '-n', 'iot-agents', '-o', 'json']))['items']
        if {p['metadata']['name'] for p in pools} != {'iot-' + r + '-pool' for r in ROLES} or any(p['spec']['replicas'] != 2 or p['status']['replicas'] != 2 for p in pools):
            raise ValueError('Require the original three two-worker pools before adding autoscaling')
        opener = build_opener(ProxyHandler({})); svc = json.loads(command('controller-service', k + ['get', 'service', 'kagent-controller', '-n', 'iot-agents', '-o', 'json']))
        with opener.open('http://' + svc['spec']['clusterIP'] + ':8083/api/substrate/status', timeout=20) as response: inventory = json.load(response)
        if any(a['status'] not in {'Suspended', 'Paused'} for a in inventory['data']['actors']): raise ValueError('Wait for active conversations to finish')
        (output / 'actors-before.json').write_text(json.dumps(inventory, indent=2) + '\n')
        lock = json.loads((source / 'infra/agents/keda.lock.json').read_bytes()); chart = output / 'keda-2.21.0.tgz'
        if lock['chart']['url'] != 'https://kedacore.github.io/charts/keda-2.21.0.tgz': raise ValueError('Require the reviewed official KEDA chart')
        with opener.open(lock['chart']['url'], timeout=45) as response: raw = response.read()
        if sha256(raw).hexdigest() != lock['chart']['sha256']: raise ValueError('Official chart digest differs')
        chart.write_bytes(raw); values = source / 'infra/agents/keda-values.yaml'
        rendered = command('keda-rendered', [helm, 'template', 'iot-agent-keda', str(chart), '-n', 'iot-autoscale', '-f', str(values), '--include-crds'])
        deployments = [d for d in yaml.safe_load_all(rendered) if d and d['kind'] == 'Deployment']
        images = {c['image'] for d in deployments for c in d['spec']['template']['spec']['containers']}
        if images != {row['image'] for row in lock['images'].values()}: raise ValueError('Rendered KEDA images are not exactly digest-pinned')
        scalers = command('scalers-rendered', [helm, 'template', 'iot-agent-scaling', str(source / 'infra/charts/iot-agent-scaling'), '-n', 'iot-agents'])
        validate_scalers(list(yaml.safe_load_all(scalers)))
        phase = 'scale-selector-compatibility'
        all_pools = json.loads(command('all-pools', k + ['get', 'workerpools', '-A', '-o', 'json']))['items']
        if len(all_pools) != 3 or any(p['metadata']['namespace'] != 'iot-agents' for p in all_pools):
            raise ValueError('Do not change a shared CRD used by unrelated pools')
        crd = json.loads(command('workerpool-crd-before', k + ['get', 'crd', 'workerpools.ate.dev', '-o', 'json']))
        command('workerpool-crd-patch', k + ['patch', 'crd', 'workerpools.ate.dev', '--type=json', '--patch-file=/dev/stdin'], json.dumps(selector_patch(crd)).encode())
        time.sleep(3)
        for role in sorted(ROLES):
            # The pinned upstream controller replaces status with a typed replicas-only object.
            # Keep this standard HPA selector in spec so subsequent replica-status updates preserve it.
            name = 'iot-' + role + '-pool'; payload = json.dumps({'spec': {'selector': 'ate.dev/worker-pool=' + name}}).encode()
            command(role + '-selector', k + ['patch', 'workerpool', name, '-n', 'iot-agents', '--type=merge', '--patch-file=/dev/stdin'], payload)
            scale = json.loads(command(role + '-scale', k + ['get', '--raw', '/apis/ate.dev/v1alpha1/namespaces/iot-agents/workerpools/' + name + '/scale']))
            if scale['status'].get('selector') != 'ate.dev/worker-pool=' + name: raise ValueError('Native scale selector did not persist')
        phase = 'keda-install'
        if repair_existing:
            actual_values = json.loads(command('keda-current-values', [helm, 'get', 'values', 'iot-agent-keda', '-n', 'iot-autoscale', '-o', 'json']))
            if actual_values != yaml.safe_load(values.read_bytes()): raise ValueError('Preserve a different live KEDA configuration')
        else:
            command('keda-install', [helm, 'install', 'iot-agent-keda', str(chart), '-n', 'iot-autoscale', '--create-namespace', '-f', str(values), '--atomic', '--wait', '--timeout', '6m'])
        command('keda-pods', k + ['get', 'pods', '-n', 'iot-autoscale', '-o', 'json'])
        phase = 'scaler-install'
        command('server-validation', k + ['apply', '--dry-run=server', '-n', 'iot-agents', '-f', '-'], scalers)
        command('scaler-install', [helm, 'upgrade', '--install', 'iot-agent-scaling', str(source / 'infra/charts/iot-agent-scaling'), '-n', 'iot-agents', '--atomic', '--wait', '--timeout', '3m'])
        command('scalers-ready', k + ['wait', '--for=condition=Ready', 'scaledobject', '-n', 'iot-agents', '-l', 'iot.coursework/agent-scaling=true', '--timeout=180s'])
        actual = json.loads(command('scalers-after', k + ['get', 'scaledobjects', '-n', 'iot-agents', '-l', 'iot.coursework/agent-scaling=true', '-o', 'json']))['items']
        validate_scalers(actual)
        deadline = time.monotonic() + 120; attempt = 0
        while True:
            attempt += 1
            actual = json.loads(command('health-' + str(attempt), k + ['get', 'scaledobjects', '-n', 'iot-agents', '-l', 'iot.coursework/agent-scaling=true', '-o', 'json']))['items']
            if all(any(c['type'] == 'HPAActive' and c['status'] == 'True' for c in d['status']['conditions'])
                   and all(v['status'] == 'Happy' for v in d['status'].get('health', {}).values()) for d in actual): break
            if time.monotonic() >= deadline: raise TimeoutError('Do not accept Ready while metrics or HPA remain unhealthy')
            time.sleep(5)
        command('hpa-after', k + ['get', 'hpa', '-n', 'iot-agents', '-o', 'json'])
        report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'keda_version': '2.21.0',
            'scalers': sorted(d['metadata']['name'] for d in actual), 'native_burst_and_scale_in_verified': False, 'coursework_complete': False,
            'scope': 'Pinned private KEDA and three Ready demand-driven scalers installed. Real role-specific bursts, worker readiness and scale-in still require native proof.'}
    except Exception as error:
        (output / 'verification.json').write_text(json.dumps({'passed': False, 'phase': phase, 'error_type': type(error).__name__,
            'scope': 'Inspect both releases and retained CRDs before retry. Releases are individually atomic; no cross-release rollback is claimed.'}, indent=2) + '\n')
        raise
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True); parser.add_argument('--source-root', type=Path, required=True); parser.add_argument('--run', required=True)
    parser.add_argument('--repair-existing', action='store_true', help='Repair only the two exact observed owned releases, preserving the installed KEDA image/configuration')
    args = parser.parse_args(); run(args.runtime_root, args.source_root, args.run, args.repair_existing)
