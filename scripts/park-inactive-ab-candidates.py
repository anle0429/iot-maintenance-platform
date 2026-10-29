"""Reclaim scheduler reservations from three failed historical coursework A/B candidates without deleting releases or data."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os
import subprocess
import time

NAMES = ['iot-ab-serve-jenkins-' + str(n) for n in [6, 7, 8]]


def reviewed_candidate(deployment, pods, ready_endpoint_uids):
    """Protect serving or unrelated workloads; accept only the known Helm candidates with no ready traffic endpoint."""
    metadata = deployment['metadata']; name = metadata['name']
    if (name not in NAMES or metadata['namespace'] != 'iot-features'
            or metadata['annotations'].get('meta.helm.sh/release-name') != name
            or metadata['labels'].get('app.kubernetes.io/managed-by') != 'Helm'
            or deployment['spec']['replicas'] not in [0, 1] or deployment['status'].get('availableReplicas', 0) != 0):
        raise ValueError('Preserve a different or serving deployment')
    for pod in pods:
        statuses = pod['status'].get('containerStatuses', [])
        if (pod['metadata']['uid'] in ready_endpoint_uids or not statuses
                or any(c.get('ready') or c.get('restartCount', 0) < 100 for c in statuses)):
            raise ValueError('Preserve a healthy, new or endpoint-serving candidate')
    if deployment['spec']['replicas'] == 1 and not pods: raise ValueError('Inspect an unobserved candidate before parking')
    return True


def run(root, output):
    """Validate all candidates first, race-guard each scale operation and retain exact scale commands and measured identities."""
    root, output = root.resolve(), output.resolve()
    if os.geteuid() != 0 or not output.is_relative_to(root / 'outputs'): raise ValueError('Use sudo and a fresh project output')
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'; k = ['/usr/local/bin/k3s', 'kubectl']
    output.mkdir(exist_ok=False)

    def get(args):
        """Read bounded Kubernetes state without printing environments, secrets or whole Pod manifests."""
        return json.loads(subprocess.check_output(k + args + ['-o', 'json'], timeout=30))

    slices = get(['get', 'endpointslices', '-n', 'iot-features'])['items']
    ready = {e.get('targetRef', {}).get('uid') for row in slices for e in row['endpoints'] if e.get('conditions', {}).get('ready') is not False}
    reviewed = []; identities = []
    for name in NAMES:
        deployment = get(['get', 'deployment', name, '-n', 'iot-features'])
        pods = get(['get', 'pods', '-n', 'iot-features', '-l', 'app.kubernetes.io/instance=' + name])['items']
        reviewed_candidate(deployment, pods, ready); reviewed.append(deployment)
        identities.append({'name': name, 'uid': deployment['metadata']['uid'], 'resource_version': deployment['metadata']['resourceVersion'],
            'replicas': deployment['spec']['replicas'], 'available': deployment['status'].get('availableReplicas', 0),
            'containers': [{'name': c['name'], 'image': c['image'], 'resources': c['resources']} for c in deployment['spec']['template']['spec']['containers']],
            'pods': [{'name': p['metadata']['name'], 'uid': p['metadata']['uid'], 'restarts': [c['restartCount'] for c in p['status']['containerStatuses']], 'ready_endpoint': False} for p in pods]})
    (output / 'reviewed-candidates.json').write_text(json.dumps(identities, indent=2) + '\n')
    changed = []
    for deployment in reviewed:
        name = deployment['metadata']['name']
        if deployment['spec']['replicas'] == 0: continue
        args = k + ['scale', 'deployment', name, '-n', 'iot-features', '--replicas=0', '--current-replicas=1', '--resource-version=' + deployment['metadata']['resourceVersion']]
        result = subprocess.run(args, capture_output=True, timeout=30)
        (output / (name + '.stdout')).write_bytes(result.stdout); (output / (name + '.stderr')).write_bytes(result.stderr)
        if result.returncode: raise RuntimeError('Scale race or operation failure; preserve receipts and inspect before retry')
        changed.append(name)
    deadline = time.monotonic() + 90
    while any(get(['get', 'pods', '-n', 'iot-features', '-l', 'app.kubernetes.io/instance=' + name])['items'] for name in NAMES):
        if time.monotonic() >= deadline: raise TimeoutError('Wait for owned inactive Pods to terminate normally')
        time.sleep(2)
    report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'changed_to_zero': changed,
              'deleted_resources': False, 'serving_endpoints_affected': False,
              'scope': 'Only three observed failed historical A/B Helm candidates were parked. Releases, Services, data, healthy APIs and other applications remain in place. Restore explicitly before repeating those old A/B releases.'}
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); run(args.runtime_root, args.output)
