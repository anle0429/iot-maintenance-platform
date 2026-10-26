"""Start private Airflow/DataHub on the existing Linux VM; persist credentials outside Git."""

import base64
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import time
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def start() -> None:
    """Create secrets once, validate Compose, migrate Airflow and start bounded services."""
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo on the Linux coursework VM (Docker socket access is required).')
    if not all(Path(path).is_mount() for path in ('/srv/iot-data', '/srv/iot-fast')):
        raise SystemExit('Both coursework data/scratch volumes must be mounted before deploying services.')
    secret_dir = ROOT / 'data/runtime/secrets'
    secret_dir.mkdir(parents=True, exist_ok=True)
    env_file = secret_dir / 'orchestration.env'
    if not env_file.exists():
        values = {'IOT_ROOT': str(ROOT), 'DOCKER_GID': str(os.stat('/var/run/docker.sock').st_gid),
                  'AIRFLOW_DB_PASSWORD': secrets.token_hex(24), 'AIRFLOW_JWT_SECRET': secrets.token_hex(32),
                  'AIRFLOW_API_SECRET': secrets.token_hex(32),
                  'AIRFLOW_FERNET_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
                  'DATAHUB_DB_PASSWORD': secrets.token_hex(24), 'DATAHUB_UI_SECRET': secrets.token_hex(32)}
        env_file.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        os.chmod(env_file, 0o600)
    values = dict(line.split('=', 1) for line in env_file.read_text().splitlines() if line)
    for key in ('DATAHUB_TOKEN_SERVICE_SALT', 'DATAHUB_TOKEN_SERVICE_SIGNING_KEY', 'DATAHUB_SYSTEM_CLIENT_SECRET'):
        values.setdefault(key, secrets.token_hex(32))
    env_file.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
    os.chmod(env_file, 0o600)
    credentials = secret_dir / 'orchestration-users.json'
    if not credentials.exists():
        credentials.write_text(json.dumps({'airflow': secrets.token_hex(16), 'datahub': secrets.token_hex(16)}))
        os.chmod(credentials, 0o600)
    users = json.loads(credentials.read_text())
    for name, content in [('airflow-users.json', json.dumps({'learner': users['airflow']})),
                          ('datahub-users.props', f"learner:{users['datahub']}\n")]:
        path = secret_dir / name
        if not path.exists():
            path.write_text(content)
        os.chmod(path, 0o660 if name.startswith('airflow') else 0o644)
    for name in ('data/runtime/airflow-logs', 'outputs/orchestration', 'data/orchestrated'):
        path = ROOT / name
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o2777)
    scratch = Path('/srv/iot-fast/orchestration-scratch')
    scratch.mkdir(exist_ok=True)
    os.chmod(scratch, 0o1777)
    subprocess.run(['docker', 'network', 'inspect', 'iot-metadata'], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=False)
    if subprocess.run(['docker', 'network', 'inspect', 'iot-metadata'], capture_output=True).returncode:
        subprocess.run(['docker', 'network', 'create', 'iot-metadata'], check=True)

    def compose(file: str, *args: str) -> None:
        """Keep actual credentials in a protected env file rather than process arguments/log output."""
        print(f'Compose {file}: {args[0]}', flush=True)
        subprocess.run(['docker', 'compose', '--env-file', str(env_file), '-f', str(ROOT / 'infra' / file),
                        *args], check=True, cwd=ROOT)

    compose('datahub.compose.yaml', 'config', '--quiet')
    compose('orchestration.compose.yaml', 'config', '--quiet')
    compose('orchestration.compose.yaml', 'build')
    subprocess.run(['docker', 'build', '-t', 'iot-coursework-metadata:1.6.0',
                    '-f', str(ROOT / 'infra/datahub/Dockerfile'), str(ROOT)], check=True)
    compose('datahub.compose.yaml', 'up', '-d')
    compose('orchestration.compose.yaml', 'run', '--rm', 'airflow-init')
    compose('orchestration.compose.yaml', 'up', '-d', 'airflow-api', 'airflow-scheduler', 'airflow-dag-processor')
    for attempt in range(60):
        try:
            with urlopen('http://127.0.0.1:18080/health', timeout=5):
                break
        except OSError:
            time.sleep(5)
    else:
        raise RuntimeError('DataHub GMS did not become healthy')
    subprocess.run(['docker', 'run', '--rm', '--network', 'iot-metadata', '-e', f'PYTHONPATH={ROOT}',
                    '-v', f'{ROOT}:{ROOT}:ro', '-w', str(ROOT), 'iot-coursework-metadata:1.6.0', '--bootstrap-user'], check=True)
    (ROOT / 'outputs/orchestration/services-started.json').write_text(json.dumps({'started': True}))
    print('Services started; verify health before triggering DAGs.', flush=True)


if __name__ == '__main__':
    start()
