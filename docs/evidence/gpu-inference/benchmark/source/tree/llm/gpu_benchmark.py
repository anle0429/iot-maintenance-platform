"""Prepare pinned weights and compare CPU/GPU inference on one private GPU worker without changing the agent route."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from statistics import median
from urllib.request import Request, ProxyHandler, build_opener
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import time
from llm.benchmark import WORKLOAD, streamed_request


def file_digest(path):
    """Hash model or source files incrementally rather than holding multi-gigabyte weights in memory."""
    value = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def verified_source(source):
    """Require exact sealed inputs and reject traversal before any model download or container operation."""
    manifest = json.loads((source.parent / 'manifest.json').read_bytes())
    required = {'llm/__init__.py', 'llm/benchmark.py', 'llm/gpu_benchmark.py',
                'infra/llm/assistant.lock.json', 'infra/llm/gpu-runtime.lock.json'}
    if not required.issubset(manifest['files']):
        raise ValueError('The source seal must cover the benchmark and both runtime locks')
    for name, pin in manifest['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or file_digest(path) != pin:
            raise ValueError('The sealed source differs: ' + name)
    return manifest


def resource_gate(root, deadline, available, free, now):
    """Require enough real RAM, SSD and approved session time for the fixed 24-request comparison."""
    remaining = (deadline - now).total_seconds()
    if (root != Path('/srv/iot-inference') or not 2700 <= remaining <= 28800
            or available < 10 * 1024**3 or free < 15 * 1024**3):
        raise ValueError('Require the dedicated worker root, 45 session minutes, 10 GiB RAM and 15 GiB free disk')
    return {'remaining_session_seconds': remaining, 'available_memory_bytes': available,
            'free_disk_bytes': free, 'deadline_utc': deadline.isoformat()}


def comparison(cpu, gpu):
    """Compare paired measured requests, rejecting changed prompts, parameters, missing usage or cached tokens."""
    if cpu['parameters'] != gpu['parameters'] or cpu['workload'] != gpu['workload']:
        raise ValueError('CPU and GPU must use exactly the same workload and generation parameters')
    for batch in [cpu, gpu]:
        if len(batch['warmup']) != 3 or len(batch['measured']) != 9:
            raise ValueError('Require three warm-up and nine measured requests per mode')
        if [(r['repeat'], r['prompt_index']) for r in batch['measured']] != [(r, p) for r in range(3) for p in range(3)]:
            raise ValueError('Measured requests must retain the same complete paired order')
        for row in batch['warmup'] + batch['measured']:
            usage = row.get('usage', {})
            if (row.get('status') != 200 or not row.get('text') or not 0 < row['ttft_seconds'] <= row['elapsed_seconds']
                    or any(not math.isfinite(row[key]) for key in ['ttft_seconds', 'elapsed_seconds'])
                    or any(type(usage.get(key)) is not int or usage[key] <= 0 for key in ['prompt_tokens', 'completion_tokens', 'total_tokens'])
                    or usage['total_tokens'] != usage['prompt_tokens'] + usage['completion_tokens']
                    or usage.get('prompt_tokens_details', {}).get('cached_tokens') != 0):
                raise ValueError('Require actual successful uncached generation and token/timing receipts')
    summary = {}
    for name, batch in [('cpu', cpu), ('gpu', gpu)]:
        rows = batch['measured']
        summary[name] = {'requests': len(rows), 'median_ttft_seconds': median(r['ttft_seconds'] for r in rows),
            'median_elapsed_seconds': median(r['elapsed_seconds'] for r in rows),
            'reported_completion_tokens': sum(r['usage']['completion_tokens'] for r in rows)}
    summary['elapsed_speedup_ratio'] = summary['cpu']['median_elapsed_seconds'] / summary['gpu']['median_elapsed_seconds']
    summary['ttft_speedup_ratio'] = summary['cpu']['median_ttft_seconds'] / summary['gpu']['median_ttft_seconds']
    summary['paired_texts_identical'] = all(a['text'] == b['text'] for a, b in zip(cpu['measured'], gpu['measured']))
    return summary


def run(root, source, run_name, phase, deadline, project):
    """Operate only on the metadata-verified GPU worker and retain original process, image, GPU and stream receipts."""
    root, source = root.resolve(), source.resolve()
    if os.name != 'posix' or os.geteuid() != 0 or not re.fullmatch(r'gpu-[a-z0-9-]+', run_name):
        raise ValueError('Use sudo on the dedicated Linux GPU worker and a fresh gpu-* run')
    opener = build_opener(ProxyHandler({}))
    identity = {}
    for key, route in [('project', 'project/project-id'), ('name', 'instance/name'), ('id', 'instance/id')]:
        with opener.open(Request('http://metadata.google.internal/computeMetadata/v1/' + route,
            headers={'Metadata-Flavor': 'Google'}), timeout=5) as response:
            identity[key] = response.read().decode()
    if identity['name'] != 'iot-cw-gpu' or identity['project'] != project:
        raise ValueError('Refuse any host except the authorized dedicated GPU worker')
    if not source.is_relative_to(root / 'source'):
        raise ValueError('Keep the immutable source under the dedicated worker root')
    seal = verified_source(source)
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))) * 1024
    budget = resource_gate(root, deadline, available, shutil.disk_usage(root).free, datetime.now(timezone.utc))
    out = root / 'outputs' / run_name
    out.mkdir(parents=True, exist_ok=False)

    def keep(name, value):
        """Retain strict public JSON measurements without model weights or credential reads."""
        (out / name).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')

    def command(label, values, timeout=300):
        """Record bounded native stdout, stderr and status, retaining failed commands for diagnosis."""
        result = subprocess.run(values, capture_output=True, timeout=timeout)
        (out / (label + '.stdout')).write_bytes(result.stdout)
        (out / (label + '.stderr')).write_bytes(result.stderr)
        (out / (label + '.exit')).write_text(str(result.returncode) + '\n')
        if result.returncode:
            raise RuntimeError(label + ' failed; inspect original receipts')
        return result.stdout

    keep('identity.json', identity); keep('source.json', seal); keep('budget.json', budget)
    lock = json.loads((source / 'infra/llm/assistant.lock.json').read_bytes())
    runtime = json.loads((source / 'infra/llm/gpu-runtime.lock.json').read_bytes())
    keep('model-lock.json', lock); keep('runtime-lock.json', runtime)
    models = root / 'models' / lock['served_model']; models.mkdir(parents=True, exist_ok=True)
    report = {'passed': False, 'phase': phase, 'agent_route_changed': False, 'coursework_complete': False}
    try:
        if phase == 'prepare':
            downloaded = []
            for member in lock['files']:
                path = models / member['file']
                if not path.exists():
                    partial = path.with_suffix('.partial')
                    if partial.exists():
                        raise ValueError('Preserve an interrupted model download before explicitly recovering it')
                    url = 'https://huggingface.co/' + lock['model_id'] + '/resolve/' + lock['revision'] + '/' + member['file']
                    with opener.open(url, timeout=120) as response, partial.open('xb') as target:
                        shutil.copyfileobj(response, target, length=1024 * 1024)
                    if partial.stat().st_size != member['bytes'] or file_digest(partial) != member['sha256']:
                        raise ValueError('Downloaded model bytes differ from the pinned artifact')
                    partial.rename(path); path.chmod(0o644)
                if path.stat().st_size != member['bytes'] or file_digest(path) != member['sha256']:
                    raise ValueError('Existing model bytes differ; preserve them for diagnosis')
                downloaded.append({'file': member['file'], 'bytes': path.stat().st_size, 'sha256': file_digest(path)})
            keep('model-files.json', downloaded)
            report.update(passed=True, actual_model_bytes=sum(row['bytes'] for row in downloaded))
        else:
            for member in lock['files']:
                path = models / member['file']
                if path.stat().st_size != member['bytes'] or file_digest(path) != member['sha256']:
                    raise ValueError('Prepare and verify both model shards first')
            devices = command('gpu-before', ['nvidia-smi', '--query-gpu=name,uuid,driver_version,memory.total', '--format=csv,noheader']).decode().strip().splitlines()
            if len(devices) != 1 or int(devices[0].split(',')[-1].strip().split()[0]) < 14_000:
                raise ValueError('Require exactly one device with at least 14000 MiB VRAM')
            command('cpu', ['lscpu', '-J'])
            command('docker-version', ['docker', 'version', '--format', '{{json .}}'])
            command('image-pull', ['docker', 'pull', runtime['image']], timeout=900)
            keep('image.json', json.loads(command('image-inspect', ['docker', 'image', 'inspect', runtime['image']]))[0])
            batches = {}
            for mode, layers in [('cpu', 0), ('gpu', 99)]:
                name = 'iot-' + run_name + '-' + mode
                values = ['docker', 'run', '-d', '--name', name, '--gpus', 'all', '--cpus', '3', '--memory', '10g',
                    '--memory-swap', '10g', '--user', '10001:10001', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                    '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=128m', '-p', '127.0.0.1:18084:8080',
                    '--mount', 'type=bind,src=' + str(models) + ',dst=/models,readonly', runtime['image'],
                    '--model', '/models/' + lock['files'][0]['file'], '--alias', lock['served_model'], '--host', '0.0.0.0',
                    '--port', '8080', '--ctx-size', '8192', '--threads', '3', '--threads-batch', '3', '--parallel', '1',
                    '--n-gpu-layers', str(layers), '--jinja', '--metrics', '--no-webui']
                keep(mode + '-command.json', values)
                command(mode + '-start', values)
                try:
                    checks = []; expires = time.monotonic() + 420
                    while True:
                        try:
                            with opener.open('http://127.0.0.1:18084/health', timeout=5) as response:
                                checks.append({'status': response.status, 'body': json.load(response)})
                                if response.status == 200: break
                        except OSError as error:
                            checks.append({'error_type': type(error).__name__})
                        if time.monotonic() >= expires:
                            keep(mode + '-health.json', checks)
                            raise TimeoutError('The measured model container did not become healthy')
                        time.sleep(2)
                    keep(mode + '-health.json', checks)
                    body = {'model': lock['served_model'], 'messages': [], 'max_tokens': 64, 'temperature': 0,
                        'seed': 7, 'cache_prompt': False, 'stream': True, 'stream_options': {'include_usage': True}}
                    batch = {'parameters': body, 'workload': WORKLOAD, 'warmup': [], 'measured': []}
                    keep(mode + '-requests.json', batch)
                    for prompt in WORKLOAD:
                        batch['warmup'].append(streamed_request('http://127.0.0.1:18084', dict(body, messages=[{'role': 'user', 'content': prompt}])))
                        keep(mode + '-requests.json', batch)
                    for repeat in range(3):
                        for index, prompt in enumerate(WORKLOAD):
                            row = streamed_request('http://127.0.0.1:18084', dict(body, messages=[{'role': 'user', 'content': prompt}]))
                            row.update(repeat=repeat, prompt_index=index); batch['measured'].append(row)
                            keep(mode + '-requests.json', batch)
                    command(mode + '-gpu-observation', ['nvidia-smi', '--query-compute-apps=pid,used_memory', '--format=csv,noheader'])
                    batches[mode] = batch
                finally:
                    command(mode + '-logs', ['docker', 'logs', name])
                    keep(mode + '-inspect.json', json.loads(command(mode + '-inspect', ['docker', 'inspect', name]))[0])
                    command(mode + '-stop', ['docker', 'stop', '--time', '15', name])
                state = json.loads(command(mode + '-stopped', ['docker', 'inspect', name]))[0]['State']
                keep(mode + '-stopped.json', state)
                if state['OOMKilled']:
                    raise ValueError('A measured container was killed for memory pressure')
            report.update(comparison(batches['cpu'], batches['gpu']), passed=True,
                scope='One GPU host and CUDA image; 0 versus 99 GPU layers. Three warm-up and nine measured requests per mode; fixed prompts, seed, token cap, context and CPU/memory limits. No agent quality, concurrency or production capacity claim.')
        report['checked_utc'] = datetime.now(timezone.utc).isoformat()
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
        keep('verification.json', report)
        raise
    keep('verification.json', report); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--phase', choices=['prepare', 'benchmark'], required=True)
    parser.add_argument('--deadline', type=datetime.fromisoformat, required=True)
    parser.add_argument('--project', required=True)
    args = parser.parse_args()
    run(args.runtime_root, args.source_root, args.run, args.phase, args.deadline, args.project)
