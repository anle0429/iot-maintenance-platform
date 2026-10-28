"""Run the label-free offline monitoring batch in an isolated, immutable Feast SDK image."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from ml.drift_monitoring import utc


def execute(run_id, interval_end, try_number):
    """Trust local deployment configuration, preserving each attempt and failing the DAG on native errors."""
    import docker
    root = Path(os.environ['IOT_ROOT']).resolve()
    deployment = json.loads((root / 'data/runtime/drift-monitoring/config.json').read_bytes())
    def local(name):
        value = Path(deployment[name])
        if value.is_absolute() or '..' in value.parts:
            raise ValueError('Use project-relative monitoring inputs')
        path = (root / value).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Monitoring input escaped project')
        return path
    interval_end = utc(interval_end).isoformat()
    if type(try_number) is not int or try_number < 1:
        raise ValueError('Require a real task attempt number')
    source = local('source_root')
    manifest = json.loads((source.parent / 'manifest.json').read_bytes())
    for name, expected in manifest['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Sealed monitoring source changed')
    label = sha256(run_id.encode()).hexdigest()[:24] + '-try' + str(try_number)
    output = root / 'outputs/offline-drift' / label
    output.mkdir(parents=True, exist_ok=False)
    # The already-running Airflow user owns this new directory; the SDK uses that same UID/GID.
    volumes = {str(source): {'bind': '/src', 'mode': 'ro'}, str(output): {'bind': '/output', 'mode': 'rw'},
        str(root / 'data/feast/batch-medium-27'): {'bind': '/workspace/data/feast/batch-medium-27', 'mode': 'ro'},
        str(root / 'outputs/feast-batch-27/batch/report.json'): {'bind': '/inputs/batch-report.json', 'mode': 'ro'},
        str(local('reference')): {'bind': '/inputs/reference.json', 'mode': 'ro'},
        str(local('clock')): {'bind': '/inputs/clock.json', 'mode': 'ro'}}
    command = ['-m', 'feature_store.drift_monitor', '--state', '/workspace/data/feast/batch-medium-27',
        '--batch-report', '/inputs/batch-report.json', '--reference', '/inputs/reference.json',
        '--config', '/inputs/clock.json', '--output', '/output/result', '--interval-end', interval_end]
    client = docker.from_env()
    image = deployment['image_id']
    if not re.fullmatch('sha256:[0-9a-f]{64}', image):
        raise ValueError('Pin the observed immutable Feast image ID')
    container = client.containers.run(image, command, entrypoint=['python'], detach=True,
        name='iot-offline-drift-' + label, user=str(os.getuid()) + ':' + str(os.getgid()),
        network_mode='host', working_dir='/src', read_only=True, cap_drop=['ALL'],
        security_opt=['no-new-privileges:true'], mem_limit='3g', memswap_limit='3g', nano_cpus=1_000_000_000,
        tmpfs={'/tmp': 'rw,nosuid,nodev,size=268435456'}, volumes=volumes,
        environment={'PYTHONPATH': '/src', 'PYTHONDONTWRITEBYTECODE': '1', 'FEAST_USAGE': 'False',
                     'HOME': '/tmp', 'IOT_FEAST_REDIS': '127.0.0.1:1', 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'},
        labels={'iot.pipeline': 'offline-drift', 'iot.run': label})
    try:
        # Independent container deadline; logs are retained on success or timeout, never silently credited.
        result = container.wait(timeout=240)
        raw = container.logs()
        (output / 'container.txt').write_bytes(raw)
        print(raw.decode(errors='replace'), flush=True)
        container.reload()
        receipt = {'run_id': run_id, 'interval_end': interval_end, 'try_number': try_number,
            'container_id': container.id, 'image_id': container.attrs['Image'], 'state': container.attrs['State'],
            'host_config': {k: container.attrs['HostConfig'][k] for k in ['Memory', 'MemorySwap', 'NanoCpus', 'ReadonlyRootfs', 'NetworkMode']},
            'source_commit': manifest['commit'], 'exit_code': result['StatusCode']}
        (output / 'container.json').write_text(json.dumps(receipt, indent=2) + '\n')
        if result['StatusCode'] != 0:
            raise RuntimeError('Offline drift batch failed; retained attempt is ' + str(output))
        return {'output': str(output), 'source_commit': manifest['commit'], 'image_id': receipt['image_id']}
    finally:
        container.reload()
        if container.status == 'running':
            container.stop(timeout=10)
        (output / 'container-final.txt').write_bytes(container.logs())
        container.remove()
        client.close()
