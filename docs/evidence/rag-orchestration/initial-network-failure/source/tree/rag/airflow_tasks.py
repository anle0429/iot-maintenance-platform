"""Launch only source-sealed RAG stages from trusted run manifests; keep heavy SDKs outside Airflow."""
from hashlib import sha256
from pathlib import Path
import json
import os
import re


def load_release(root, run_id):
    """Accept a bounded identifier, verify exact source bytes and derive fixed state/output locations."""
    if not re.fullmatch(r'rag-[a-z0-9][a-z0-9-]{1,50}', run_id): raise ValueError('Invalid RAG run identifier')
    root = Path(root).resolve(); manifest = json.loads((root / 'data/rag-releases' / (run_id + '.json')).read_bytes())
    if manifest['run_id'] != run_id or manifest['schema_version'] != 1: raise ValueError('RAG release identity differs')
    source = (root / manifest['source_directory']).resolve()
    if not source.is_relative_to(root / 'data/llm-source'): raise ValueError('The source must remain within the sealed lab snapshots')
    for name, expected in manifest['source_files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != expected: raise ValueError('RAG source seal mismatch')
    for name in ['image_id', 'metadata_image_id']:
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', manifest[name]): raise ValueError('Immutable image IDs are required')
    return manifest, source, root / 'data/rag' / run_id, root / 'outputs' / run_id


def execute(operation, run_id):
    """Run a bounded native container and persist its actual state without deleting failures or force-retrying writers."""
    if operation not in ['fetch', 'chunk', 'embed', 'store', 'validate', 'publish']: raise ValueError('Unknown RAG operation')
    import docker
    root = Path(os.environ['IOT_ROOT']).resolve(); manifest, source, state, reports = load_release(root, run_id)
    if not state.is_dir() or not reports.is_dir(): raise ValueError('The trusted operator must prepare a new state/report directory')
    metadata = operation == 'publish'; image = manifest['metadata_image_id'] if metadata else manifest['image_id']
    module = 'rag.metadata_publish' if metadata else 'rag.quality' if operation == 'validate' else 'rag.pipeline'
    args = ['--state', '/state', '--run', run_id] if metadata else ['--source', '/workspace', '--output', '/state']
    if operation not in ['validate', 'publish']: args.insert(0, operation)
    environment = {'PYTHONPATH': '/workspace', 'HF_HOME': '/models', 'HF_HUB_OFFLINE': '1', 'FEAST_USAGE': 'False',
                   'OMP_NUM_THREADS': '2', 'OPENBLAS_NUM_THREADS': '1', 'TOKENIZERS_PARALLELISM': 'false'}
    if metadata: environment['DATAHUB_GMS_URL'] = 'http://datahub-gms:8080'
    volumes = {str(source): {'bind': '/workspace', 'mode': 'ro'}, str(state): {'bind': '/state', 'mode': 'rw'},
               '/srv/iot-fast/models/iot-rag-embeddings': {'bind': '/models', 'mode': 'rw'}}
    client = docker.from_env(); container = None
    try:
        options = {'network': 'iot-metadata'} if metadata else {'network_mode': 'none'}
        container = client.containers.create(image, args,
            entrypoint=['/usr/bin/timeout', '--signal=TERM', '--kill-after=10s', '600', 'python', '-m', module],
            name='iot-' + run_id + '-' + operation, working_dir='/workspace', environment=environment, volumes=volumes,
            user='10001:10001', read_only=True, mem_limit='2g' if metadata else '4g', memswap_limit='2g' if metadata else '4g',
            nano_cpus=1_000_000_000 if metadata else 2_000_000_000, pids_limit=256, cap_drop=['ALL'], security_opt=['no-new-privileges'],
            tmpfs={'/tmp': 'rw,nosuid,size=512m'}, init=True, labels={'iot.rag.run': run_id, 'iot.rag.operation': operation}, **options)
        container.start(); print(json.dumps({'container_id': container.id, 'operation': operation}), flush=True)
        result = container.wait(timeout=660); container.reload()
        logs = container.logs(); (reports / (operation + '.log')).write_bytes(logs)
        with (reports / (operation + '-container.json')).open('x') as stream: json.dump(container.attrs, stream, indent=2)
        print(logs.decode(errors='replace'), flush=True)
        if result['StatusCode'] != 0 or container.attrs['State']['OOMKilled']: raise RuntimeError('Native RAG stage failed; inspect its retained container and logs')
        return {'operation': operation, 'container_id': container.id, 'image_id': container.attrs['Image']}
    finally:
        client.close()
