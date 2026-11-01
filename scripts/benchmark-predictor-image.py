"""Measure full/slim Python images for identical predictor bytes and native Spark golden requests on an approved CPU VM."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import time


def digest(path):
    """Bind a small public input file to its actual bytes."""
    return sha256(path.read_bytes()).hexdigest()


def save(path, value):
    """Preserve finite structured measurements without overwriting an earlier run directory."""
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')


def reserve(deadline):
    """Refuse insufficient host resources or less than ten minutes of authorized VM time."""
    mem = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')}
    assert mem['MemAvailable'] > 8 * 1024**3, 'Preserve an 8 GiB available-RAM reserve'
    assert shutil.disk_usage('/srv/iot-fast').free > 15 * 1024**3, 'Preserve 15 GiB of SSD space'
    assert datetime.now(timezone.utc) + timedelta(minutes=10) < deadline, 'Approved session is nearly over'
    return mem['MemAvailable']


def run(args):
    """Build two new images, compare real HTTP behavior in network-isolated containers and retain all native outputs."""
    assert os.name == 'posix' and all(Path(p).is_mount() for p in ['/srv/iot-data', '/srv/iot-fast'])
    desc = json.loads(args.descriptor.read_text(encoding='utf-8-sig'))
    assert desc['name'] == 'iot-cw-vm' and desc['status'] == 'RUNNING' and desc['machineType'].endswith('/e2-highmem-8')
    assert desc['scheduling']['maxRunDuration']['seconds'] == '28800' and desc['scheduling']['instanceTerminationAction'] == 'STOP'
    deadline = datetime.fromisoformat(desc['lastStartTimestamp']) + timedelta(hours=8)
    reserve(deadline)
    root, source, out = args.runtime_root.resolve(), args.source.resolve(), args.output.resolve()
    assert source.is_relative_to(root) and out.is_relative_to(root / 'outputs') and not out.exists()
    out.mkdir(parents=True); context = out / 'context'; context.mkdir()
    wanted = ['services/__init__.py', 'ml/__init__.py', 'ml/protocol.py', 'ml/training_contract.py',
              'infra/predictor/Dockerfile', 'infra/predictor/Dockerfile.dockerignore', 'infra/predictor/requirements.lock',
              'infra/benchmarks/PredictorBaseline.Dockerfile', 'tests/fixtures/predictor-native-spark.json']
    wanted += [p.relative_to(source).as_posix() for p in sorted((source / 'services/predictor').glob('*.py'))]
    wanted += ['models/pump-failure-v1/' + name for name in ['portable.json', 'lineage.json']]
    inputs = []
    for name in sorted(set(wanted)):
        original = source / name
        assert original.is_file() and not original.is_symlink()
        target = context / name; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(original, target)
        inputs.append({'path': name, 'bytes': target.stat().st_size, 'sha256': digest(target)})
    # The baseline and optimized contexts have the same explicit public-file allowlist.
    shutil.copyfile(context / 'infra/predictor/Dockerfile.dockerignore', context / '.dockerignore')
    save(out / 'inputs.json', {'files': inputs, 'public_context_bytes': sum(i['bytes'] for i in inputs),
                              'source_only': True, 'datasets_credentials_and_git_excluded': True})

    def command(name, argv, timeout=1800):
        """Archive native stdout and guard the client without signalling unrelated containers or the Docker daemon."""
        started = time.monotonic()
        with (out / (name + '.txt')).open('wb') as log:
            process = subprocess.Popen(argv, cwd=context, stdout=log, stderr=subprocess.STDOUT)
            try:
                while process.poll() is None:
                    reserve(deadline)
                    if time.monotonic() - started > timeout: raise TimeoutError(name)
                    time.sleep(1)
            finally:
                if process.poll() is None:
                    process.terminate(); process.wait(timeout=30)
        record = {'arguments': argv, 'exit_code': process.returncode, 'seconds': time.monotonic() - started}
        save(out / (name + '.json'), record)
        assert process.returncode == 0, name + ' failed; original output retained'
        return record

    def inspect(image):
        """Read exact image size and platform; never confuse it with compressed registry transfer size."""
        d = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image]))[0]
        version = next(v.split('=', 1)[1] for v in d['Config']['Env'] if v.startswith('PYTHON_VERSION='))
        return {'id': d['Id'], 'bytes': d['Size'], 'platform': d['Os'] + '/' + d['Architecture'], 'python_version': version}

    # Match the Python patch level of the digest-pinned optimized image.
    optimized_dockerfile = (context / 'infra/predictor/Dockerfile').read_text()
    optimized_base = next(line.split()[1] for line in optimized_dockerfile.splitlines() if line.startswith('FROM '))
    command('pull-optimized-base', ['docker', 'pull', optimized_base])
    base_info = inspect(optimized_base)
    full_tag = 'python:' + base_info['python_version'] + '-bookworm'
    command('pull-full-base', ['docker', 'pull', full_tag])
    full = json.loads(subprocess.check_output(['docker', 'image', 'inspect', full_tag]))[0]
    full_digest = next(x for x in full['RepoDigests'] if x.startswith('python@sha256:'))
    assert inspect(full_digest)['python_version'] == base_info['python_version']
    save(out / 'bases.json', {'optimized': optimized_base, 'baseline': full_digest, 'python_version': base_info['python_version']})
    tags = {'baseline': 'iot-review-predictor-baseline:' + args.run,
            'optimized': 'iot-review-predictor-optimized:' + args.run}
    images = {}
    for variant, dockerfile in [('baseline', 'infra/benchmarks/PredictorBaseline.Dockerfile'), ('optimized', 'infra/predictor/Dockerfile')]:
        argv = ['docker', 'build', '--no-cache', '--progress=plain', '-t', tags[variant], '-f', dockerfile]
        if variant == 'baseline': argv += ['--build-arg', 'BASE_IMAGE=' + full_digest]
        measured = command('build-' + variant, argv + ['.'])
        images[variant] = {**inspect(tags[variant]), 'application_layer_build_seconds': measured['seconds']}
        command('history-' + variant, ['docker', 'history', '--no-trunc', tags[variant]])
    assert images['baseline']['python_version'] == images['optimized']['python_version']

    golden = json.loads((context / 'tests/fixtures/predictor-native-spark.json').read_text())
    model_path = context / 'models/pump-failure-v1/portable.json'
    assert golden['passed'] and golden['portable_sha256'] == digest(model_path)
    probe_code = '''import json,sys,time,urllib.request,urllib.error
payload=json.loads(sys.argv[1])
for i in range(60):
 try:
  ready=json.load(urllib.request.urlopen('http://127.0.0.1:8080/health/ready',timeout=2));break
 except OSError:time.sleep(.25)
else:raise RuntimeError('readiness timeout')
live=json.load(urllib.request.urlopen('http://127.0.0.1:8080/health/live'))
body=json.dumps({'instances':[x['features'] for x in payload['samples']]}).encode()
request=urllib.request.Request('http://127.0.0.1:8080/v1/models/'+payload['model_name']+':predict',data=body,headers={'Content-Type':'application/json'})
result=json.load(urllib.request.urlopen(request,timeout=10))
print(json.dumps({'ready':ready,'live':live,'result':result},sort_keys=True))
'''
    responses = {}
    for variant, tag in tags.items():
        name = 'iot-image-review-' + variant + '-' + args.run
        assert not subprocess.run(['docker', 'inspect', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0, 'Use fresh container names'
        try:
            command('start-' + variant, ['docker', 'run', '-d', '--name', name, '--network', 'none', '--cpus', '1', '--memory', '512m',
                    '--pids-limit', '128', '--read-only', '--tmpfs', '/tmp:rw,nosuid,nodev,size=32m', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges', tag], timeout=120)
            command('probe-' + variant, ['docker', 'exec', name, 'python', '-c', probe_code, json.dumps(golden)], timeout=90)
            response = json.loads((out / ('probe-' + variant + '.txt')).read_text())
            assert response['ready']['ready'] and response['live']['live']
            predictions = response['result']['predictions']
            assert len(predictions) == len(golden['samples'])
            delta = max(abs(x['risk_score'] - y['expected_risk_score']) for x, y in zip(predictions, golden['samples']))
            assert delta < 1e-12 and all(x['predicted_failure'] == y['expected_predicted_failure'] for x, y in zip(predictions, golden['samples']))
            responses[variant] = {'passed': True, 'native_http_predictions': len(predictions), 'maximum_golden_score_delta': delta,
                                  'http_response_sha256': digest(out / ('probe-' + variant + '.txt'))}
        finally:
            subprocess.run(['docker', 'stop', '--time', '10', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    left = json.loads((out / 'probe-baseline.txt').read_text())
    right = json.loads((out / 'probe-optimized.txt').read_text())
    assert left == right, 'Full and slim images returned different health/model/prediction values'
    before, after = images['baseline']['bytes'], images['optimized']['bytes']
    assert before > after
    for item in inputs: assert digest(source / item['path']) == item['sha256'], 'Source changed during benchmark'
    report = {'passed': True, 'completed_utc': datetime.now(timezone.utc).isoformat(), 'images': images, 'http_checks': responses,
              'saved_bytes': before - after, 'reduction_percent': 100 * (before - after) / before,
              'exact_http_responses_equal': True, 'source_unchanged': True,
              'scope': 'Constructed full-Python baseline vs scoped slim serving image; only base-image choice differs. Same Python patch, application, lock and model. App layers rebuilt without cache; base pull excluded from build timing. Uncompressed local Docker Size, not compressed transfer bytes or production latency.'}
    save(out / 'verification.json', report); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root', 'source', 'output', 'descriptor']: parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    assert args.run.isalnum() and len(args.run) < 30
    run(args)
