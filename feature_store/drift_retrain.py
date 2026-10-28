"""Run the opt-in drift-to-KFP controller in its pinned SDK image after native monitoring succeeds."""
from hashlib import sha256
from pathlib import Path
import json, os, re


def execute(monitoring, run_id, try_number, manual_probe=False):
    """Keep Airflow lightweight, preserve each container attempt and never resubmit an uncertain KFP intent."""
    import docker
    root = Path(os.environ['IOT_ROOT']).resolve()
    config_path = root / 'data/runtime/drift-retrain/config.json'
    if not config_path.exists():
        if manual_probe: raise ValueError('Deploy a reviewed retrain recipe before a manual probe')
        return {'action': 'skipped', 'reason': 'retrain_not_configured'}
    if type(manual_probe) is not bool or type(try_number) is not int or try_number < 1:
        raise ValueError('Explicit probe and positive task attempt required')
    config = json.loads(config_path.read_bytes())
    def local(value):
        path = Path(value); resolved = (root / path).resolve()
        if path.is_absolute() or '..' in path.parts or not resolved.is_relative_to(root):
            raise ValueError('Use project-relative retrain configuration')
        return resolved
    source = local(config['source_root'])
    manifest = json.loads((source.parent / 'manifest.json').read_bytes())
    for name, expected in manifest['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Sealed retrain control source changed')
    report = Path(monitoring['output']).resolve() / 'result'
    if not report.is_relative_to(root / 'outputs/offline-drift') or not (report / 'completion.json').is_file():
        raise ValueError('A successful upstream native monitoring receipt is required')
    image = config['controller_image_id']
    if not re.fullmatch('sha256:[0-9a-f]{64}', image): raise ValueError('Pin the verified KFP controller image ID')
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
        '--policy', str(local(config['policy'])), '--state', str(state), '--output', str(output / 'decision.json')]
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
        receipt = {'run_id':run_id, 'try_number':try_number, 'source_commit':manifest['commit'],
            'manual_probe':manual_probe, 'monitoring_output':str(report), 'container_id':container.id,
            'image_id':container.attrs['Image'], 'state':container.attrs['State'], 'exit_code':result['StatusCode'],
            'host_config':{k:container.attrs['HostConfig'][k] for k in ['Memory','MemorySwap','NanoCpus','ReadonlyRootfs','NetworkMode']}}
        (output / 'container.json').write_text(json.dumps(receipt, indent=2)+'\n')
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
