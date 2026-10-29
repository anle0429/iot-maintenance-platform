"""Prepare truthful fresh Feast state and atomically switch only the existing model6 feature API in the retained lab."""
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.request import build_opener, ProxyHandler
import argparse
import base64
import json
import os
import re
import shutil
import subprocess


def digest(path):
    """Hash large retained inputs incrementally instead of reading their full size into memory."""
    value = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): value.update(block)
    return value.hexdigest()


def run(args):
    """Keep model/images/checkpoints unchanged; prepare a new seed, or explicitly resume deployment of its fresh state."""
    if os.name != 'posix' or os.geteuid() != 0: raise ValueError('Run inside the existing Linux lab VM with sudo')
    root = args.runtime_root.resolve(); source = args.runtime_source.resolve()
    if not root.is_relative_to('/srv/iot-data') or not source.is_relative_to(root / 'data') or not re.fullmatch(r'demo-[a-z0-9-]{1,40}', args.run):
        raise ValueError('Use the existing lab runtime source and a new demo-* identifier')
    output = args.output.resolve(); target = root / 'data/demo' / args.run; state = target / 'state'
    if not output.is_relative_to(root / 'outputs'): raise ValueError('Keep new receipts under project outputs/')
    if output.exists() or (target.exists() != args.resume_deploy): raise ValueError('Choose a fresh output/state; use --resume-deploy only for an existing prepared state')
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    if (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds() < 2400: raise ValueError('At least40minutes in the approved session are required')
    if not all(Path(p).is_mount() for p in ['/srv/iot-data', '/srv/iot-fast']): raise ValueError('Keep the two existing data mounts')
    ram = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))) * 1024
    if ram < 16 * 1024**3 or shutil.disk_usage(root).free < 20_000_000_000: raise ValueError('Require16GiB RAM available and20GB free on the data disk')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    if not all(digest(source / name) == pin for name, pin in seal['files'].items()): raise ValueError('The sealed runtime source changed')
    k = ['/usr/local/bin/k3s', 'kubectl']; helm = str(root / 'data/runtime/kubernetes-assets-27/helm/helm')
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'
    def command(values):
        """Capture command output without shell interpolation or credential-bearing environment dumps."""
        return subprocess.check_output(values, stderr=subprocess.PIPE)
    def get(*values):
        """Read actual Kubernetes JSON for specifically named private lab resources."""
        return json.loads(command(k + list(values) + ['-o', 'json']))
    pods = get('get', 'pods', '-n', 'kubeflow')['items']
    if any(p['status']['phase'] in ['Running', 'Pending'] and 'pipeline/runid' in p['metadata'].get('labels', {}) for p in pods): raise ValueError('Wait for the active training pipeline')
    values = json.loads(command([helm, 'get', 'values', 'iot-feature-api', '-n', 'iot-features', '--all', '-o', 'json']))
    env = {item['name']: item.get('value') for item in values['env']}
    if env['IOT_KSERVE_URL'] != 'http://iot-pump-serve-jenkins-8-predictor.iot-serving.svc.cluster.local' or values['image']['tag'] != 'observe-jenkins-2':
        raise ValueError('This refresh is scoped to the already deployed model6/telemetry2 lab release')
    redis_service = get('get', 'service', 'online-serve-jenkins-8', '-n', 'iot-features')
    if redis_service['spec'].get('type', 'ClusterIP') != 'ClusterIP': raise ValueError('Keep Redis private')
    redis = redis_service['spec']['clusterIP']
    bundle = root / 'data/ci/releases/serve-jenkins-8/bundle'; pin = json.loads((bundle / 'model-pin.json').read_bytes())
    if pin['version'] != '6' or pin['production'] is not True or digest(bundle / 'portable.json') != pin['portable_sha256']: raise ValueError('Preserve the current model6 bundle')
    output.mkdir(parents=True, exist_ok=False)
    report = {'passed': False, 'source_sha256': digest(Path(__file__)), 'runtime_source': seal, 'run': args.run, 'model_version': '6', 'resume_deploy': args.resume_deploy}
    def save(name, value):
        """Retain strict JSON phase receipts in the new output directory."""
        (output / name).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    try:
        sdk = json.loads(command(['docker', 'image', 'inspect', 'iot-coursework-feast-versioned:0.67.0-3.17.2']))[0]['Id']
        probe = "import json,redis; r=redis.Redis(host=" + repr(redis) + ",port=6379,socket_timeout=2); k=list(r.scan_iter(match=b'*iot_serving_demo',count=256)); print(json.dumps({'keys':len(k),'pttl_ms':[r.pttl(x) for x in k]}))"
        def ttl(name):
            """Measure actual cache key count and expiry without reading or exporting feature values."""
            result = json.loads(command(['docker', 'run', '--rm', '--network', 'host', '--cpus', '.5', '--memory', '256m', '--entrypoint', 'python', sdk, '-c', probe]))
            save(name, result); return result
        before = ttl('physical-ttl-before.json')
        if not args.resume_deploy:
            if before['keys'] != 0: raise ValueError('Preserve live keys; reuse the ready demo or wait for natural expiry')
            target.mkdir(parents=True, exist_ok=False); os.chown(target, 10001, 10001)
            protocol = json.loads((source / 'ml/protocols/medium-60d-v1.json').read_bytes())
            cutoff = (datetime.fromisoformat(protocol['chronological_split']['observation_end_exclusive'].replace('Z', '+00:00')) - timedelta(minutes=10)).isoformat()
            arguments = ['docker', 'run', '--name', 'iot-' + args.run, '--network', 'host', '--memory', '8g', '--memory-swap', '8g', '--cpus', '1', '--entrypoint', 'python',
                '--env', 'PYTHONPATH=/src', '--env', 'IOT_FEAST_REDIS=' + redis + ':6379', '--env', 'FEAST_USAGE=False', '--env', 'OMP_NUM_THREADS=1', '--env', 'OPENBLAS_NUM_THREADS=1',
                '--mount', f'type=bind,src={source},dst=/src,readonly', '--mount', f'type=bind,src={root},dst={root},readonly', '--mount', f'type=bind,src={target},dst={target}', sdk, '-m', 'feature_store.serving',
                '--state', str(state), '--source-state', str(root / 'data/feast/batch-medium-27'), '--batch-report', str(root / 'outputs/feast-batch-27/batch/report.json'),
                '--protocol', '/src/ml/protocols/medium-60d-v1.json', '--silver', str(root / 'data/lakehouse/large-ingest-medium-27-v4'), '--input-binding', str(root / 'outputs/feature-medium-27-v2/input-binding.json'), '--cutoff', cutoff]
            save('materialization-command.json', arguments)
            with (output / 'materialization.stdout').open('wb') as log, (output / 'materialization.stderr').open('wb') as errors:
                subprocess.run(arguments, stdout=log, stderr=errors, check=True, timeout=1200)
            inspected = json.loads(command(['docker', 'inspect', 'iot-' + args.run]))[0]
            if inspected['State']['ExitCode'] != 0 or inspected['State']['OOMKilled'] or inspected['Image'] != sdk: raise ValueError('Materialization container failed')
            save('materialization-container.json', {'id': inspected['Id'], 'image': inspected['Image'], 'state': inspected['State'],
                'user': inspected['Config']['User'], 'memory_bytes': inspected['HostConfig']['Memory'], 'memory_swap_bytes': inspected['HostConfig']['MemorySwap'],
                'cpu_nanos': inspected['HostConfig']['NanoCpus'], 'mounts': [{'source': m['Source'], 'destination': m['Destination'], 'rw': m['RW']} for m in inspected['Mounts']]})
        context = json.loads((state / 'context.json').read_bytes()); verification = json.loads((state / 'verification.json').read_bytes()); ready = json.loads((state / '_READY.json').read_bytes())
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(context['prepared_wall_utc'])).total_seconds()
        if not verification['passed'] or not 0 <= age < 3000 or ready['context_sha256'] != digest(state / 'context.json') or ready['verification_sha256'] != digest(state / 'verification.json'):
            raise ValueError('Use the original valid fresh prepared state, not an edited context or expired retry')
        after = ttl('physical-ttl-after.json')
        if after['keys'] != 200 or min(after['pttl_ms']) < 600_000: raise ValueError('Native materialization has not populated all200machines with enough lease')
        files = {'context.json': state / 'context.json', 'verification.json': state / 'verification.json', '_READY.json': state / '_READY.json', 'registry.db': state / 'registry/registry.db'}
        existing = command(k + ['get', 'configmap', args.run, '-n', 'iot-features', '--ignore-not-found', '-o', 'name']).decode().strip()
        if existing:
            found = get('get', 'configmap', args.run, '-n', 'iot-features')
            for name, path in files.items():
                data = found.get('data', {}).get(name)
                raw = data.encode() if data is not None else base64.b64decode(found['binaryData'][name])
                if raw != path.read_bytes(): raise ValueError('Preserve a different existing seed ConfigMap')
        else:
            seed = json.loads(command(k + ['create', 'configmap', args.run, '-n', 'iot-features', '--dry-run=client', '-o', 'json'] + ['--from-file=' + name + '=' + str(path) for name, path in files.items()]))
            seed['immutable'] = True; subprocess.run(k + ['create', '-f', '-'], input=json.dumps(seed).encode(), check=True, stdout=subprocess.DEVNULL)
        values['revisionMarker'] = args.run; next(v for v in values['volumes'] if v['name'] == 'seed')['configMap']['name'] = args.run
        save('values.json', values)
        with (output / 'helm.stdout').open('wb') as log:
            subprocess.run([helm, 'upgrade', 'iot-feature-api', str(source / 'infra/charts/iot-api'), '-n', 'iot-features', '-f', str(output / 'values.json'), '--atomic', '--wait', '--timeout', '240s'], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
        service = get('get', 'service', 'iot-feature-api', '-n', 'iot-features'); opener = build_opener(ProxyHandler({}))
        with opener.open('http://' + service['spec']['clusterIP'] + '/health/ready', timeout=10) as response: health = json.load(response)
        if health.get('ready') is not True: raise ValueError('The refreshed API is not ready')
        report.update(passed=True, prepared_wall_utc=context['prepared_wall_utc'], lease_expires_utc=(datetime.fromisoformat(context['prepared_wall_utc']) + timedelta(hours=1)).isoformat(),
            seed_uid=get('get', 'configmap', args.run, '-n', 'iot-features')['metadata']['uid'], sdk_image_id=sdk,
            checked_utc=datetime.now(timezone.utc).isoformat(), machines=200, ready=health,
            scope='Actual fresh native materialization and atomic seed refresh, same model6/images/data. Run demo-feature-api.py separately for HTTP rejection and independent prediction arithmetic.')
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    save('verification.json', report); print(json.dumps(report, indent=2)); return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--runtime-source', type=Path, required=True)
    parser.add_argument('--run', required=True); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume-deploy', action='store_true')
    raise SystemExit(run(parser.parse_args()))
