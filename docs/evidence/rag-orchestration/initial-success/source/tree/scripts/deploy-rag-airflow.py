"""Prepare a sealed RAG release, deploy only its DAG/module link and verify the real Airflow/DataHub run."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import argparse
import json
import os
import re
import shutil
import subprocess
import time


def deploy(args):
    """Keep data, credentials and prior runs separate while adding the selected RAG workload to existing Airflow."""
    root = args.runtime_root.resolve(); source = args.source_root.resolve(); run_id = args.run
    if os.name != 'posix' or os.geteuid() != 0 or not root.is_relative_to('/srv/iot-data') or not source.is_relative_to(root / 'data/llm-source'):
        raise ValueError('Use sudo in the retained lab with a sealed source under data/llm-source')
    if not re.fullmatch(r'rag-[a-z0-9][a-z0-9-]{1,50}', run_id): raise ValueError('Invalid RAG run identifier')
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    remaining = (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds()
    memory = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))) * 1024
    if remaining < 2400 or memory < 10 * 1024**3: raise ValueError('Require 40 minutes in the approved session and 10 GiB available memory')
    output = root / 'outputs' / run_id; state = root / 'data/rag' / run_id
    release_path = root / 'data/rag-releases' / (run_id + '.json')
    if any(path.exists() for path in [output, state, release_path]): raise ValueError('Preserve existing release/state/output; inspect its actual run')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    for name, digest in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != digest: raise ValueError('Source seal mismatch')
    output.mkdir(parents=True); os.chown(output, 50000, 0); output.chmod(0o750)
    state.mkdir(parents=True); os.chown(state, 10001, 0); state.chmod(0o750)
    (output / 'source.json').write_text(json.dumps(seal, indent=2)+'\n')
    dependency_hash = sha256(b''.join((source / name).read_bytes() for name in ['infra/rag/Dockerfile', 'infra/rag/requirements.txt'])).hexdigest()
    images = {}
    for kind, image in [('image_id', 'iot-coursework-rag:deps-' + dependency_hash[:12]), ('metadata_image_id', 'iot-coursework-metadata:1.6.0')]:
        raw = subprocess.check_output(['docker', 'image', 'inspect', image]); info = json.loads(raw)[0]
        if kind == 'image_id' and info['Config']['Labels'].get('iot.dependencies.sha256') != dependency_hash: raise ValueError('Dependency image differs')
        images[kind] = info['Id']; (output / (kind + '.json')).write_bytes(raw)
    release_path.parent.mkdir(exist_ok=True)
    networks = json.loads(subprocess.check_output(['docker', 'inspect', 'iot-datahub-datahub-gms-1', '--format', '{{json .NetworkSettings.Networks}}']))
    matching = [(name, value) for name, value in networks.items() if name.startswith('iot-metadata') and 'datahub-gms' in (value.get('Aliases') or [])]
    if len(matching) != 1: raise ValueError('Require one actual DataHub metadata bridge with its declared DNS alias')
    network_name, network = matching[0]
    inspected_network = json.loads(subprocess.check_output(['docker', 'network', 'inspect', network['NetworkID']]))[0]
    if inspected_network['Driver'] != 'bridge' or inspected_network['Scope'] != 'local': raise ValueError('Preserve existing private bridge topology')
    (output / 'metadata-network.json').write_text(json.dumps({'name': network_name, 'connection': network, 'driver': inspected_network['Driver']}, indent=2)+'\n')
    manifest = {'schema_version': 1, 'run_id': run_id, 'source_directory': source.relative_to(root).as_posix(),
                'source_files': seal['files'], 'metadata_network_id': network['NetworkID'], **images}
    with release_path.open('x') as stream: json.dump(manifest, stream, indent=2)
    release_path.chmod(0o640)
    (output / 'release.json').write_text(json.dumps(manifest, indent=2)+'\n')
    password = json.loads((root / 'data/runtime/secrets/orchestration-users.json').read_bytes())['airflow']

    def api(path, body=None, method=None, token=None):
        """Keep auth headers/responses in memory and redact every HTTP error body."""
        headers = {'Content-Type': 'application/json'}
        if token: headers['Authorization'] = 'Bearer ' + token
        request = Request('http://127.0.0.1:18480' + path, headers=headers,
                          data=json.dumps(body).encode() if body is not None else None, method=method)
        try:
            with urlopen(request, timeout=30) as response: return json.load(response)
        except HTTPError as error: raise RuntimeError('Private Airflow HTTP ' + str(error.code)) from None

    token = api('/auth/token', {'username': 'learner', 'password': password})['access_token']; password = None
    existing = api('/api/v2/dags?limit=100', token=token)
    if 'iot_rag_knowledge' in {row['dag_id'] for row in existing['dags']}:
        runs = api('/api/v2/dags/iot_rag_knowledge/dagRuns?limit=100', token=token)['dag_runs']
        if any(row['state'] in ['queued', 'running'] for row in runs): raise ValueError('An actual RAG DAG run is active; observe it before deployment')
    link = root / 'rag'
    if link.exists() or link.is_symlink():
        if not link.is_symlink() or not link.resolve().is_relative_to(root / 'data/llm-source'): raise ValueError('Preserve the existing non-owned rag path')
        temporary = root / ('.rag-' + run_id); temporary.symlink_to(source / 'rag', target_is_directory=True); temporary.replace(link)
    else:
        link.symlink_to(source / 'rag', target_is_directory=True)
    dag_source = source / 'airflow/dags/iot_rag_knowledge.py'; target = root / 'airflow/dags/iot_rag_knowledge.py'
    if target.exists() and target.read_bytes() != dag_source.read_bytes(): raise ValueError('Preserve the different existing DAG file')
    if not target.exists(): shutil.copyfile(dag_source, target)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        dags = api('/api/v2/dags?limit=100', token=token)['dags']
        found = next((row for row in dags if row['dag_id'] == 'iot_rag_knowledge'), None)
        if found: break
        time.sleep(5)
    else: raise RuntimeError('The actual DAG did not import; inspect Airflow import errors')
    if found['is_paused']: api('/api/v2/dags/iot_rag_knowledge', {'is_paused': False}, method='PATCH', token=token)
    created = api('/api/v2/dags/iot_rag_knowledge/dagRuns', {'dag_run_id': run_id, 'logical_date': datetime.now(timezone.utc).isoformat(), 'conf': {'run_id': run_id}}, token=token)
    (output / 'airflow-created.json').write_text(json.dumps(created, indent=2)+'\n')
    path = '/api/v2/dags/iot_rag_knowledge/dagRuns/' + run_id; deadline = time.monotonic() + 900
    with (output / 'airflow-polls.jsonl').open('x') as history:
        while time.monotonic() < deadline:
            current = {'run': api(path, token=token), 'tasks': api(path + '/taskInstances', token=token)}
            history.write(json.dumps(current)+'\n'); history.flush()
            if current['run']['state'] in ['success', 'failed']: break
            time.sleep(5)
        else: raise RuntimeError('Observation deadline reached; inspect the same DAG run before any retry')
    (output / 'airflow-final.json').write_text(json.dumps(current, indent=2)+'\n')
    tasks = current['tasks']['task_instances']
    if current['run']['state'] != 'success' or len(tasks) != 6 or any(row['state'] != 'success' for row in tasks):
        raise RuntimeError('The real RAG DAG failed; preserve its task/container state')
    for name in ['quality.json', 'datahub-publication.json', 'retrieval.json', 'store-readback.json']:
        shutil.copyfile(state / name, output / name)
    result = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'run_id': run_id, 'actual_successful_airflow_tasks': 6,
              'quality_checks': len(json.loads((state / 'quality.json').read_bytes())['checks']),
              'scope': 'Actual Airflow RAG stages and native GMS lineage/schema readback. CI, failed-data branch, indexed UI/history, MCP and agents remain separate.'}
    (output / 'verification.json').write_text(json.dumps(result, indent=2)+'\n'); print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True); parser.add_argument('--run', required=True)
    deploy(parser.parse_args())
