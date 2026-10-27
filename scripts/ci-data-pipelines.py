"""Test, package, auto-deploy and verify the three real Airflow data pipelines from a sealed trusted source commit."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DAGS = {'dp1': 'iot_ci_dp1_bronze', 'dp2': 'iot_ci_dp2_warehouse', 'dp3': 'iot_ci_dp3_features'}


def save(path, value):
    """Write small native receipts atomically; never include authentication material."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def digest(path):
    """Bind the small executable release files to their exact bytes."""
    return sha256(path.read_bytes()).hexdigest()


def api(path, token=None, body=None):
    """Keep private Airflow credentials in memory and redact response bodies on HTTP errors."""
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer '+token
    request = Request(os.environ.get('IOT_AIRFLOW_URL', 'http://127.0.0.1:18480')+path, headers=headers,
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f'Private Airflow HTTP {error.code}; credentials and response body omitted') from None


def run(args):
    """Verify the sealed commit before each phase; successful CI must finish the real scheduler tasks."""
    commit = os.environ['IOT_CI_COMMIT']
    release = 'jenkins-'+os.environ['BUILD_NUMBER']+'-1'
    if os.environ.get('IOT_CI_PROVIDER') != 'jenkins' or not os.environ.get('JENKINS_URL'):
        raise ValueError('Only the authenticated private Jenkins pipeline may deploy this sealed release')
    if not re.fullmatch('[0-9a-f]{40}', commit) or not re.fullmatch(r'jenkins-[0-9]+-1', release):
        raise ValueError('Invalid release identity')
    deploy = Path(os.environ['IOT_DEPLOY_ROOT']).resolve()
    seal_path = deploy/'data/ci/source'/commit/'manifest.json'
    seal = json.loads(seal_path.read_text())
    if seal['commit'] != commit:
        raise ValueError('Source seal belongs to another Git commit')
    for name, expected in seal['files'].items():
        path = (ROOT/name).resolve()
        if not path.is_relative_to(ROOT) or digest(path) != expected:
            raise ValueError('Workspace differs from the sealed source: '+name)
    executable = {p.relative_to(ROOT).as_posix() for folder in ['generator', 'pipelines'] for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if executable != {n for n in seal['files'] if n.startswith(('generator/', 'pipelines/'))}:
        raise ValueError('Unsealed executable source was added to the workspace')
    directory = deploy/'data/ci/releases'/release
    out = ROOT/'outputs/ci-data-pipelines'
    out.mkdir(parents=True, exist_ok=True)
    if args.phase == 'test':
        started = time.monotonic()
        subprocess.run(['python3', '-m', 'unittest', 'tests.test_orchestration_contract',
            'tests.test_chunk_source', 'tests.test_chunked', '-v'], cwd=ROOT, check=True)
        for name in ['scripts/ci-data-pipelines.py', 'infra/ci/iot_ci_data_pipelines.py']:
            compile((ROOT/name).read_text(), name, 'exec')
        save(out/'test.json', {'passed': True, 'commit': commit, 'release': release, 'source_seal_sha256': digest(seal_path),
            'seconds': time.monotonic()-started,
            'scope': 'Existing known-answer generator, source receipt, corruption, replay and artifact-boundary tests. Native Spark checks also run after deployment.'})
    elif args.phase == 'build':
        if not json.loads((out/'test.json').read_text())['passed']:
            raise ValueError('Tests must pass before build')
        session = json.loads((deploy/'data/runtime/ci/session.json').read_text())
        stop = datetime.fromisoformat(session['auto_stop_utc'])
        ram = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
        if (stop-datetime.now(timezone.utc)).total_seconds() < 2400 or ram < 16*1024**3 or min(shutil.disk_usage(p).free for p in [deploy, Path('/srv/iot-fast/ci')]) < 15_000_000_000:
            raise ValueError('CI requires40minutes in the approved VM session,16GiB available RAM and15GB free on each data disk')
        directory.mkdir(parents=True, exist_ok=False)
        work = directory/'work'
        work.mkdir()
        sources = sorted(name for name in seal['files'] if name.startswith(('generator/', 'pipelines/')))
        for name in sources:
            target = work/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/name, target)
        for name in ['data/source/orchestration-pilot', 'data/orchestrated', 'outputs/orchestration']:
            (work/name).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(deploy/'outputs/orchestration/source-receipt.json', work/'outputs/orchestration/source-receipt.json')
        for path in [work/'data/orchestrated', work/'outputs']:
            path.chmod(0o1777)
        # Spark and metadata containers write distinct operation receipts under this directory.
        (work/'outputs/orchestration').chmod(0o1777)
        scratch = Path('/srv/iot-fast/ci')/release
        scratch.mkdir(parents=True, exist_ok=False)
        scratch.chmod(0o1777)
        images = {}
        for kind, runtime in [('spark', 'iot-coursework-spark:3.5.9'), ('metadata', 'iot-coursework-metadata:1.6.0')]:
            base = subprocess.check_output(['docker', 'image', 'inspect', runtime, '--format', '{{.Id}}'], text=True).strip()
            tag = 'iot-ci-'+kind+':'+release
            subprocess.run(['docker', 'build', '--build-arg', 'RUNTIME='+runtime,
                '--label', 'org.opencontainers.image.revision='+commit,
                '-f', str(ROOT/'infra/ci/DP.Dockerfile'), '-t', tag, str(ROOT)], check=True)
            image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', tag], text=True))[0]
            if image['Config']['Labels']['org.opencontainers.image.revision'] != commit:
                raise ValueError('Built image is missing its source commit')
            probe = "import hashlib,json,pathlib; root=pathlib.Path('/opt/iot-release'); print(json.dumps({str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ['generator','pipelines'] for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts}))"
            packaged = json.loads(subprocess.check_output(['docker', 'run', '--rm', '--network', 'none', '--memory', '256m', '--cpus', '.5', '--entrypoint', 'python3', image['Id'], '-c', probe], text=True))
            if packaged != {name: digest(work/name) for name in sources}:
                raise ValueError('Packaged executable source differs from the tested Git checkout')
            if subprocess.check_output(['docker', 'image', 'inspect', runtime, '--format', '{{.Id}}'], text=True).strip() != base:
                raise ValueError('Runtime base changed during build')
            images[kind] = {'id': image['Id'], 'tag': tag, 'runtime_base_id': base, 'packaged_source_verified': True}
        manifest = {'release': release, 'commit': commit, 'images': images,
            'source_sha256': {name: digest(work/name) for name in sources},
            'dag_sha256': digest(ROOT/'infra/ci/iot_ci_data_pipelines.py'),
            'built_utc': datetime.now(timezone.utc).isoformat()}
        save(directory/'release.json', manifest)
        save(out/'build.json', manifest)
    elif args.phase == 'deploy':
        manifest = json.loads((directory/'release.json').read_text())
        if manifest['dag_sha256'] != digest(ROOT/'infra/ci/iot_ci_data_pipelines.py'):
            raise ValueError('Deployment source differs from the tested release')
        target = deploy/'airflow/dags/ci/iot_ci_data_pipelines.py'
        pending = target.with_suffix('.pending')
        shutil.copyfile(ROOT/'infra/ci/iot_ci_data_pipelines.py', pending)
        pending.replace(target)
        if digest(target) != manifest['dag_sha256']:
            raise ValueError('DAG readback differs from the tested source')
        save(out/'deployment.json', {'passed': True, 'commit': commit, 'release': release,
            'dag_sha256': digest(target), 'dags': DAGS, 'deployed_utc': datetime.now(timezone.utc).isoformat()})
    elif args.phase == 'cleanup':
        ids = subprocess.check_output(['docker', 'ps', '-q', '--filter', 'label=iot.ci.release='+release], text=True).split()
        if ids:
            subprocess.run(['docker', 'stop', '--time', '20', *ids], check=True)
        save(out/'cleanup.json', {'release': release, 'stopped_only_owned_containers': ids})
    else:
        token = api('/auth/token', body={'username': os.environ.get('IOT_AIRFLOW_USER', 'learner'), 'password': os.environ['IOT_AIRFLOW_PASSWORD']})['access_token']
        dag = DAGS[args.stage]
        deadline = time.monotonic()+180
        while time.monotonic() < deadline:
            listed = api('/api/v2/dags?limit=100', token)['dags']
            if dag in {row['dag_id'] for row in listed}:
                break
            time.sleep(5)
        else:
            raise RuntimeError('The deployed CI DAG was not imported by Airflow')
        api(f'/api/v2/dags/{dag}/dagRuns', token, {'dag_run_id': release,
            'logical_date': datetime.now(timezone.utc).isoformat(), 'conf': {'release': release, 'commit': commit}})
        deadline = time.monotonic()+1200
        while time.monotonic() < deadline:
            native = api(f'/api/v2/dags/{dag}/dagRuns/{release}', token)
            tasks = api(f'/api/v2/dags/{dag}/dagRuns/{release}/taskInstances', token)
            save(out/(args.stage+'-airflow.json'), {'run': native, 'tasks': tasks})
            if native['state'] in ['success', 'failed']:
                if native['state'] != 'success' or any(row['state'] != 'success' for row in tasks['task_instances']):
                    raise RuntimeError('A deployed Airflow task failed; inspect its retained native log')
                break
            time.sleep(5)
        else:
            raise RuntimeError('CI timeout; do not treat a still-running DAG as success')
        stage = directory/'work/data/orchestrated'/release/args.stage
        validation = json.loads((stage/'validation.json').read_text())
        publication = json.loads((stage/'datahub-publication.json').read_text())
        if not validation['passed'] or not publication['datasets_read_back']:
            raise ValueError('Scheduler success without successful validation and metadata readback')
        for name in ['validation.json', 'datahub-publication.json', '_INGESTED.json']:
            shutil.copyfile(stage/name, out/(args.stage+'-'+name))
        receipts = {}
        for operation in ['ingest', 'validate', 'publish']:
            name = args.stage+'-'+operation+'-container.json'
            shutil.copyfile(directory/'work/outputs'/name, out/name)
            receipts[operation] = json.loads((out/name).read_text())
            expected = 'metadata' if operation == 'publish' else 'spark'
            if receipts[operation]['exit_code'] or receipts[operation]['image_id'] != json.loads((directory/'release.json').read_text())['images'][expected]['id']:
                raise ValueError('Deployed image identity or exit status differs from the built release')
        save(out/(args.stage+'-verification.json'), {'passed': True, 'commit': commit, 'release': release,
            'dag_id': dag, 'rules_passed': len(validation['checks']), 'containers': receipts,
            'completed_utc': datetime.now(timezone.utc).isoformat(),
            'scope': 'Real Jenkins test/build/auto-deploy, Airflow ingest/validate/DataHub publish on the continuous 20-machine48h pilot. Not100GB CI or production-model API CI.'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['test', 'build', 'deploy', 'verify', 'cleanup'])
    parser.add_argument('--stage', choices=list(DAGS))
    args = parser.parse_args()
    if args.phase == 'verify' and args.stage is None:
        parser.error('--stage is required for verification')
    run(args)
