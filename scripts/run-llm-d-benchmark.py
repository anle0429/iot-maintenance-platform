"""Run the pinned upstream llm-d-benchmark harness against the retained 7B route."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit
import argparse
import base64
import json
import os
import re
import shutil
import subprocess


REVISION = '3045e869269ec7e6d562129ae06a20b5ea86840e'  # v0.5.2
IMAGE = 'ghcr.io/llm-d/llm-d-benchmark@sha256:e7272472fb46ac6e907283ee96f5761876f3bb78f6a43ce67736ac446f19fe2f'
MODEL = 'iot-qwen2.5-7b'
ROUTER = 'http://iot-router-epp.iot-llm.svc.cluster.local:8081'
TOKENIZER_REVISION = 'a09a35458c702b33eeacc393d103063234e8bc28'
TOKENIZER_HASHES = {
    'tokenizer.json': 'c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539',
    'tokenizer_config.json': '5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583',
}


def profile(url, concurrency):
    """Prepare a small GuideLLM chat-completions load, with bounded duration and requests."""
    value = {'target': url, 'model': MODEL, 'processor': '/tokenizer',
             'profile': 'synchronous' if concurrency == 1 else 'concurrent',
             'backend_kwargs': {'validate_backend': False, 'follow_redirects': False},
             'data_request_formatter': 'chat_completions', 'max_seconds': 30, 'max_requests': 24,
             'data': {'prompt_tokens': 128, 'output_tokens': 32}}
    if concurrency == 2:
        value['rate'] = 2
    return value


def gate(root):
    """Require the retained Linux machine, approved session and actual storage/RAM reserve."""
    if os.name != 'posix' or os.geteuid() != 0 or not root.is_relative_to('/srv/iot-data'):
        raise ValueError('Execute with sudo on the retained CPU VM only')
    if not all(Path(path).is_mount() for path in ('/srv/iot-data', '/srv/iot-fast')):
        raise ValueError('Expected retained data and scratch mounts')
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    gpu = json.loads((root / 'data/runtime/ci/gpu-session.json').read_bytes())
    now = datetime.now(timezone.utc)
    remaining = (datetime.fromisoformat(session['auto_stop_utc']) - now).total_seconds()
    gpu_start = datetime.fromisoformat(gpu['last_start_utc'])
    gpu_remaining = (gpu_start + timedelta(hours=8) - now).total_seconds()
    if (gpu.get('status') != 'RUNNING' or gpu.get('termination_action') != 'STOP'
            or gpu.get('max_duration_seconds') != 28800 or gpu_start > now):
        raise ValueError('Expected the retained GPU worker within its approved STOP session')
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))) * 1024
    fast_free = shutil.disk_usage('/srv/iot-fast').free
    if min(remaining, gpu_remaining) < 1800 or available < 8 * 1024**3 or fast_free < 35 * 1024**3:
        raise ValueError('Need 30 approved minutes on both nodes, 8 GiB available RAM and 35 GiB free scratch')
    return {'remaining_seconds': remaining, 'gpu_remaining_seconds': gpu_remaining,
            'available_bytes': available, 'scratch_free_bytes': fast_free}


def route():
    """Resolve only the existing private llm-d ClusterIP and its fixed port."""
    raw = subprocess.check_output(['/usr/local/bin/k3s', 'kubectl', '-n', 'iot-llm', 'get', 'service',
                                   'iot-router-epp', '-o', 'json'], timeout=30)
    service = json.loads(raw)
    address = IPv4Address(service['spec']['clusterIP'])
    if (service['spec']['type'] != 'ClusterIP' or not address.is_private or address.is_loopback
            or 8081 not in {port['port'] for port in service['spec']['ports']}):
        raise ValueError('Expected a private retained llm-d Service on port 8081')
    return f'http://{address}:8081'


def observer_token():
    """Read the existing scoped model client key only into the parent process."""
    raw = subprocess.check_output(['/usr/local/bin/k3s', 'kubectl', '-n', 'iot-llm', 'get', 'secret',
                                   'iot-llm-observer-client', '-o', 'jsonpath={.data.API_KEY}'], timeout=20)
    token = base64.b64decode(raw, validate=True).decode('ascii')
    if not re.fullmatch(r'[A-Za-z0-9_.~+/=-]{32,256}', token):
        raise ValueError('Expected one bounded observer client bearer token')
    return token


def routed_smoke(url, token, out):
    """Keep the actual authenticated model readiness result outside the measured workload."""
    request = {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Reply with the word pump.'}],
               'stream': False, 'temperature': 0, 'max_tokens': 8}
    endpoint = urlsplit(url)
    connection = HTTPConnection(endpoint.hostname, endpoint.port, timeout=60)
    try:
        connection.request('POST', '/v1/chat/completions', json.dumps(request).encode(), {
            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        body = response.read(1024 * 1024 + 1)
        status = response.status
    finally:
        connection.close()
    if len(body) > 1024 * 1024 or token.encode() in body:
        raise ValueError('Invalid or credential-bearing smoke response')
    (out / 'model-smoke-request.json').write_text(json.dumps(request, indent=2) + '\n')
    (out / 'model-smoke-response.json').write_bytes(body)
    (out / 'model-smoke-status.txt').write_text(str(status) + '\n')
    if status != 200:
        raise ValueError(f'The routed model readiness request returned HTTP {status}')
    reply = json.loads(body)
    if reply.get('model') != MODEL or reply['usage']['completion_tokens'] <= 0:
        raise ValueError('The selected 7B model did not generate through llm-d')


@contextmanager
def authorized_relay(url, token):
    """Authenticate one fixed upstream route without putting a key in the harness profile or report."""
    endpoint = urlsplit(url)

    class RelayHandler(BaseHTTPRequestHandler):
        """Forward bounded chat requests and stream the exact upstream body."""

        def log_message(self, _format, *_args):
            """Keep request bodies and headers out of process logs."""

        def do_POST(self):
            """Check workload scope before forwarding and preserve upstream status and stream."""
            if self.path != '/v1/chat/completions':
                self.send_error(404)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 1 <= size <= 65536:
                    raise ValueError('Invalid body size')
                self.connection.settimeout(20)
                body = self.rfile.read(size)
                payload = json.loads(body)
                cap = payload.get('max_completion_tokens', payload.get('max_tokens'))
                if payload.get('model') != MODEL or type(cap) is not int or not 1 <= cap <= 64:
                    raise ValueError('Invalid benchmark model or output cap')
            except (ValueError, AttributeError, OSError):
                self.send_error(400)
                return
            upstream = HTTPConnection(endpoint.hostname, endpoint.port, timeout=60)
            try:
                upstream.request('POST', self.path, body, {'Authorization': 'Bearer ' + token,
                                 'Content-Type': 'application/json', 'Accept-Encoding': 'identity'})
                response = upstream.getresponse()
            except (OSError, HTTPException):
                upstream.close()
                self.send_error(502)
                return
            try:
                self.send_response(response.status)
                for name in ('Content-Type', 'Content-Length', 'Content-Encoding', 'X-Iot-Request-Id'):
                    if value := response.getheader(name):
                        self.send_header(name, value)
                self.end_headers()
                while chunk := response.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, HTTPException):
                return
            finally:
                upstream.close()

    with ThreadingHTTPServer(('127.0.0.1', 0), RelayHandler) as relay:
        thread = Thread(target=relay.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{relay.server_port}'
        finally:
            relay.shutdown()
            thread.join(timeout=5)


def checked_counts(path):
    """Read the native GuideLLM v0.5.3 result and refuse empty or errored trials."""
    value = json.loads(path.read_bytes())
    if value['args']['model'] != MODEL or len(value['benchmarks']) != 1:
        raise ValueError('Unexpected model or number of benchmark phases')
    counts = value['benchmarks'][0]['scheduler_metrics']['requests_made']
    good, incomplete, errors, total = (counts[key] for key in ('successful', 'incomplete', 'errored', 'total'))
    if (any(type(count) is not int or count < 0 for count in (good, incomplete, errors, total))
            or not 1 <= good <= total <= 24 or total != good + incomplete + errors or errors):
        raise ValueError('Native workload was empty, exceeded its bound or returned errors')
    return counts


def native_verification(out, exit_code):
    """Validate the raw workload and classify only the observed optional converter failure separately."""
    artifacts = sorted((out / 'results').rglob('results.json'))
    if len(artifacts) != 1:
        raise ValueError('Expected exactly one original GuideLLM report')
    native = artifacts[0]
    counts = checked_counts(native)
    stdout = (out / 'harness.stdout').read_text(encoding='utf-8', errors='replace')
    stderr = (out / 'harness.stderr').read_text(encoding='utf-8', errors='replace')
    first = sorted(native.parent.glob('benchmark_report,_results.json_*.yaml'))
    second = sorted(native.parent.glob('benchmark_report_v0.2,_results.json_*.yaml'))
    if 'Harness completed successfully.' not in stdout or 'Harness returned with error' in stdout or len(first) != 1:
        raise ValueError('Expected the completed upstream workload and its standard v0.1 report')
    v02 = 'complete'
    if exit_code == 0 and len(second) == 1:
        pass
    elif (exit_code == 1 and not second and 'Converting results.json to Benchmark Report v0.2' in stdout
          and 'Results data conversion completed with errors.' in stdout
          and stderr.count('Traceback (most recent call last):') == 1
          and 'File "/opt/benchmark_report/native_to_br0_2.py"' in stderr
          and 'if not native.get("config"):' in stderr
          and "AttributeError: 'NoneType' object has no attribute 'get'" in stderr):
        v02 = 'failed_missing_harness_pod_metadata'
    else:
        raise ValueError('Unrecognized upstream exit or missing standard report')
    return {
        'native_workload_passed': True,
        'native_results_sha256': sha256(native.read_bytes()).hexdigest(),
        'requests': counts,
        'upstream_exit': exit_code,
        'standard_report_v01_sha256': sha256(first[0].read_bytes()).hexdigest(),
        'standard_report_v02': v02,
        'scope': 'One routed synthetic client trial with explicit incomplete requests and converter status.',
    }


def main():
    """Write a reviewable profile, or execute the pre-pulled upstream image and retain native output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, default=Path.cwd())
    parser.add_argument('--run', required=True)
    parser.add_argument('--concurrency', type=int, choices=[1, 2], default=1)
    parser.add_argument('--tokenizer', type=Path, help='Local, verified Qwen2.5-7B-Instruct tokenizer directory')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'llmdb-[a-z0-9-]{1,35}', args.run):
        raise ValueError('Use a unique llmdb- run name')
    root = args.runtime_root.resolve()
    out = root / 'outputs' / args.run
    if out.exists():
        raise ValueError('Choose a new run name; preserve all prior results')
    if not args.execute:
        run_profile(args, out, ROUTER)
        return
    budget = gate(root)
    if args.tokenizer is None:
        raise ValueError('Supply the verified local tokenizer, not the GGUF weights')
    tokenizer = args.tokenizer.resolve(strict=True)
    if not tokenizer.is_relative_to('/srv/iot-fast/models') or ',' in str(tokenizer):
        raise ValueError('Use the retained model disk and a valid Docker bind path')
    tokenizer_hashes = {name: sha256((tokenizer / name).read_bytes()).hexdigest() for name in TOKENIZER_HASHES}
    if tokenizer_hashes != TOKENIZER_HASHES:
        raise ValueError('Tokenizer files do not match the pinned upstream revision')
    subprocess.run(['docker', 'image', 'inspect', IMAGE], stdout=subprocess.DEVNULL, timeout=20, check=True)
    upstream = route()
    token = observer_token()
    with authorized_relay(upstream, token) as relay_url:
        run_profile(args, out, relay_url, upstream=upstream, token=token, budget=budget,
                    tokenizer=tokenizer, tokenizer_hashes=tokenizer_hashes)


def run_profile(args, out, url, *, upstream=None, token=None, budget=None, tokenizer=None, tokenizer_hashes=None):
    """Write the exact upstream profile and preserve native attempts under a fresh run name."""
    out.mkdir(parents=True, mode=0o755, exist_ok=False)
    profile_dir = out / 'guidellm'
    profile_dir.mkdir()
    workload = profile_dir / 'iot-small-chat.yaml'
    workload.write_text(json.dumps(profile(url, args.concurrency), indent=2) + '\n', encoding='utf-8')
    report = {'mode': 'execute' if args.execute else 'render-only', 'upstream_revision': REVISION,
              'harness_image': IMAGE, 'profile_sha256': sha256(workload.read_bytes()).hexdigest(),
              'concurrency': args.concurrency, 'budget': budget, 'tokenizer_revision': TOKENIZER_REVISION,
              'tokenizer_sha256': tokenizer_hashes, 'upstream_route': upstream,
              'authenticated_loopback_relay': args.execute}
    (out / 'plan.json').write_text(json.dumps(report, indent=2) + '\n')
    if not args.execute:
        print(out)
        return
    routed_smoke(upstream, token, out)
    results = out / 'results'
    results.mkdir(mode=0o750)
    os.chown(results, 10001, 10001)
    for path in (profile_dir, results):
        if ',' in str(path):
            raise ValueError('Invalid Docker bind path')
    cidfile = out / 'container.cid'
    command = ['docker', 'run', '--rm', '--name', args.run, '--cidfile', str(cidfile),
               '--pull=never', '--network=host',
               '--cpus=2', '--memory=3g', '--pids-limit=192', '--user=10001:10001', '--read-only',
               '--cap-drop=ALL', '--security-opt=no-new-privileges', '--tmpfs=/tmp:rw,nosuid,nodev,size=512m',
               '--workdir=/requests', '--mount', f'type=bind,src={profile_dir},dst=/workspace/profiles/guidellm,readonly',
               '--mount', f'type=bind,src={results},dst=/requests',
               '--mount', f'type=bind,src={tokenizer},dst=/tokenizer,readonly']
    env = {'HOME': '/tmp', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'OMP_NUM_THREADS': '2',
           'TORCHINDUCTOR_CACHE_DIR': '/tmp/iot-bench-torch',
           'MPLCONFIGDIR': '/tmp', 'LLMDBENCH_RUN_WORKSPACE_DIR': '/workspace',
           'LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_PREFIX': '/requests',
           'LLMDBENCH_RUN_EXPERIMENT_ID': args.run, 'LLMDBENCH_HARNESS_STACK_NAME': 'iot-llm-d-7b',
           'LLMDBENCH_RUN_EXPERIMENT_HARNESS_MAX_TRIES': '1',
           'LLMDBENCH_VLLM_COMMON_METRICS_SCRAPE_ENABLED': 'false'}
    for key, value in env.items():
        command += ['--env', key + '=' + value]
    command += [IMAGE, '--harness', 'guidellm', '--workload', workload.name]
    (out / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
    with (out / 'harness.stdout').open('wb') as stdout, (out / 'harness.stderr').open('wb') as stderr:
        try:
            result = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=480)
        except subprocess.TimeoutExpired:
            # Stop only the immutable ID created by this invocation.
            cid = cidfile.read_text().strip() if cidfile.is_file() else ''
            if re.fullmatch(r'[0-9a-f]{64}', cid):
                subprocess.run(['docker', 'stop', '-t', '3', cid], timeout=20, check=True)
            raise
    (out / 'harness.exit').write_text(str(result.returncode) + '\n')
    accepted = native_verification(out, result.returncode)
    (out / 'verification.json').write_text(json.dumps(accepted, indent=2) + '\n')
    print(out / 'verification.json')


if __name__ == '__main__':
    main()
