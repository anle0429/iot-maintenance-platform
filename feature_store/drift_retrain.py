"""Hand off verified monitoring output to the separate Airflow candidate-training run."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from ml.drift_retrain import admission_reason, digest, local, read, validate

CONFIG = 'data/runtime/drift-retrain/config.json'
DISPATCH_RECEIPT = 'retrain-dispatch.json'
BOUND_FILES = ('container.json', 'result/completion.json', 'result/report.json',
               'result/retrieval.json', 'result/metrics.prom')


def _output(root, attempt):
    """Resolve a monitoring attempt without accepting absolute paths or redirects."""
    if not isinstance(attempt, str) or not re.fullmatch(r'[0-9a-f]{24}-try[1-9][0-9]*', attempt):
        raise ValueError('Invalid monitoring dispatch attempt')
    output = root / 'outputs/offline-drift' / attempt
    if output.resolve() != output:
        raise ValueError('Invalid monitoring dispatch directory')
    return output


def _bindings(output):
    """Hash the exact native report inputs and container receipt to be consumed by the worker."""
    artifacts = {name: output / name for name in BOUND_FILES}
    if any(path.resolve() != path or not path.is_file() for path in artifacts.values()):
        raise ValueError('A successful upstream native monitoring receipt is required')
    return {name: digest(path) for name, path in artifacts.items()}


def _deployment(root):
    """Bind the chosen recipe, controller packet and image before dispatch."""
    raw = (root / CONFIG).read_bytes()
    config = json.loads(raw)
    source = local(root, config['source_root'])
    policy = local(root, config['policy'])
    image = config['controller_image_id']
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError('Pin the verified KFP controller image ID')
    bindings = {'deployment_sha256': sha256(raw).hexdigest(),
                'controller_manifest_sha256': digest(source.parent / 'manifest.json'),
                'policy_sha256': digest(policy)}
    return source, policy, image, bindings


def _persist_dispatch(path, receipt):
    """Publish a fully written receipt once, and allow an identical dispatch-task replay."""
    data = (json.dumps(receipt, sort_keys=True, allow_nan=False) + '\n').encode()
    with NamedTemporaryFile(dir=path.parent, prefix='.dispatch-', delete=False) as stream:
        pending = Path(stream.name)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        try:
            if os.name == 'nt':
                pending.rename(path)
            else:
                os.link(pending, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data:
                raise ValueError('Conflicting monitoring dispatch receipt') from None
        if os.name == 'posix':
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        pending.unlink(missing_ok=True)


def prepare_dispatch(monitoring, run_id, manual_probe=False):
    """Return a child DAG configuration only for validated drift or an approved manual probe."""
    if type(manual_probe) is not bool:
        raise ValueError('Explicit probe boolean required')
    root = Path(os.environ['IOT_ROOT']).resolve()
    if not (root / CONFIG).is_file():
        if manual_probe:
            raise ValueError('Deploy a reviewed retrain recipe before a manual probe')
        print('KFP candidate dispatch skipped: retrain_not_configured', flush=True)
        return None

    reported_output = Path(monitoring['output'])
    attempt = reported_output.name
    output = _output(root, attempt)
    if reported_output != output:
        raise ValueError('A successful upstream native monitoring receipt is required')
    files = _bindings(output)
    native = read(output / 'container.json')
    try_number = native['try_number']
    if (
        type(try_number) is not int or try_number < 1
        or attempt != f'{sha256(run_id.encode()).hexdigest()[:24]}-try{try_number}'
        or native['run_id'] != run_id
        or native['source_commit'] != monitoring['source_commit']
        or native['image_id'] != monitoring['image_id']
        or native['exit_code'] != 0 or native['state']['ExitCode'] != 0
        or native['state']['OOMKilled'] is not False
    ):
        raise ValueError('A successful upstream native monitoring receipt is required')

    _, policy_path, _, deployment = _deployment(root)
    policy = read(policy_path)
    report, _ = validate(root, output / 'result', policy)
    reason = admission_reason(policy, report, manual_probe)
    if not policy['enabled'] or reason == 'no_detected_drift':
        print('KFP candidate dispatch skipped: ' + (reason if policy['enabled'] else 'policy_disabled'), flush=True)
        return None
    _persist_dispatch(output / DISPATCH_RECEIPT, {
        'format': 'iot-drift-dispatch-v1', 'monitoring_run_id': run_id, 'manual_probe': manual_probe,
        'files_sha256': files, **deployment,
    })
    return {'monitoring_attempt': attempt}


def execute(dispatch, run_id, try_number):
    """Revalidate the authorized handoff before running the pinned candidate-only controller."""
    if not isinstance(dispatch, dict) or set(dispatch) != {'monitoring_attempt'}:
        raise ValueError('Require an authorized monitoring dispatch')
    if type(try_number) is not int or try_number < 1:
        raise ValueError('Positive task attempt required')
    root = Path(os.environ['IOT_ROOT']).resolve()
    attempt = dispatch['monitoring_attempt']
    monitoring_output = _output(root, attempt)
    receipt_path = monitoring_output / DISPATCH_RECEIPT
    if run_id != 'candidate-' + attempt or receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError('Require an authorized monitoring dispatch')
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    if receipt['format'] != 'iot-drift-dispatch-v1' or type(receipt['manual_probe']) is not bool:
        raise ValueError('Invalid monitoring dispatch receipt')
    source, policy, image, deployment = _deployment(root)
    if receipt['files_sha256'] != _bindings(monitoring_output) or any(
        receipt[key] != value for key, value in deployment.items()
    ):
        raise ValueError('Monitoring inputs or controller deployment changed after dispatch')
    native = read(monitoring_output / 'container.json')
    if (
        receipt['monitoring_run_id'] != native['run_id']
        or attempt != f"{sha256(native['run_id'].encode()).hexdigest()[:24]}-try{native['try_number']}"
    ):
        raise ValueError('Monitoring dispatch does not match the original attempt')
    manual_probe = receipt['manual_probe']
    manifest = read(source.parent / 'manifest.json')
    for name, expected in manifest['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Sealed retrain control source changed')
    report = monitoring_output / 'result'

    import docker
    label = sha256(run_id.encode()).hexdigest()[:24] + '-try' + str(try_number)
    output = root / 'outputs/drift-retrain' / label; output.mkdir(parents=True, exist_ok=False)
    state = root / 'data/runtime/drift-retrain-state'; state.mkdir(parents=True, exist_ok=True)
    volumes = {str(root): {'bind': str(root), 'mode': 'ro'}, str(source): {'bind': '/control', 'mode': 'ro'},
        str(output): {'bind': str(output), 'mode': 'rw'}, str(state): {'bind': str(state), 'mode': 'rw'},
        '/srv/iot-data': {'bind': '/srv/iot-data', 'mode': 'ro'}, '/srv/iot-fast': {'bind': '/srv/iot-fast', 'mode': 'ro'},
        str(root / 'outputs/versioned-training'): {'bind': str(root / 'outputs/versioned-training'), 'mode': 'rw'},
        str(root / 'data/ml-workspaces/training-cluster-27'): {'bind': str(root / 'data/ml-workspaces/training-cluster-27'), 'mode': 'rw'},
        '/usr/local/bin/k3s': {'bind': '/usr/local/bin/k3s', 'mode': 'ro'},
        '/etc/rancher/k3s/k3s.yaml': {'bind': '/etc/rancher/k3s/k3s.yaml', 'mode': 'ro'}}
    command = ['-m', 'ml.drift_retrain', '--root', str(root), '--report', str(report),
        '--policy', str(policy), '--state', str(state), '--output', str(output / 'decision.json')]
    if manual_probe: command += ['--manual-probe']
    client = docker.from_env(); container = None
    try:
        container = client.containers.run(image, command, entrypoint=['python'], detach=True,
            name='iot-drift-retrain-' + label, user='0:0', network_mode='host', working_dir='/control',
            read_only=True, mem_limit='2g', memswap_limit='2g', nano_cpus=1_000_000_000,
            tmpfs={'/tmp': 'rw,nosuid,nodev,size=536870912',
                   '/var/lib/rancher/k3s': 'rw,exec,nosuid,nodev,size=536870912'}, volumes=volumes,
            environment={'PYTHONPATH':'/control', 'PYTHONDONTWRITEBYTECODE':'1', 'HOME':'/tmp',
                         'OMP_NUM_THREADS':'1', 'OPENBLAS_NUM_THREADS':'1'},
            labels={'iot.pipeline':'drift-retrain', 'iot.run':label})
        result = container.wait(timeout=4000); container.reload()
        container_receipt = {'run_id':run_id, 'try_number':try_number, 'source_commit':manifest['commit'],
            'monitoring_run_id': receipt['monitoring_run_id'], 'dispatch_sha256': sha256(receipt_bytes).hexdigest(),
            'manual_probe':manual_probe, 'monitoring_output':str(report), 'container_id':container.id,
            'image_id':container.attrs['Image'], 'state':container.attrs['State'], 'exit_code':result['StatusCode'],
            'host_config':{k:container.attrs['HostConfig'][k] for k in ['Memory','MemorySwap','NanoCpus','ReadonlyRootfs','NetworkMode']}}
        (output / 'container.json').write_text(json.dumps(container_receipt, indent=2)+'\n')
        if result['StatusCode'] != 0: raise RuntimeError('Drift retrain controller failed; preserve '+str(output))
        decision = json.loads((output / 'decision.json').read_bytes())
        print(json.dumps(decision), flush=True)
        return {'output':str(output), **decision}
    finally:
        if container is not None:
            container.reload()
            if container.status == 'running': container.stop(timeout=15)
            (output / 'container.txt').write_bytes(container.logs())
            container.remove()
        client.close()
