"""Build the isolated CPU dependency image and execute the five native RAG stages on the retained VM."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import json
import os
import re
import shutil
import subprocess


def execute(args):
    """Preserve sealed sources, all stage states and actual native container exit/resource receipts."""
    root = args.runtime_root.resolve(); source = args.source_root.resolve(); output = args.output.resolve()
    if os.name != 'posix' or os.geteuid() != 0 or not root.is_relative_to('/srv/iot-data'):
        raise ValueError('Run with sudo in the retained Linux lab')
    if not source.is_relative_to(root / 'data') or not output.is_relative_to(root / 'outputs') or not re.fullmatch(r'rag-[a-z0-9-]{1,40}', args.run):
        raise ValueError('Use sealed lab sources, a new output and a rag-* run name')
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    remaining = (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds()
    memory = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))) * 1024
    if remaining < 2400 or memory < 10 * 1024**3 or shutil.disk_usage('/srv/iot-fast').free < 20 * 1024**3:
        raise ValueError('Require 40 minutes in the approved session, 10 GiB available RAM and 20 GiB free SSD')
    state = root / 'data/rag' / args.run
    if state.exists() or output.exists(): raise ValueError('Preserve prior state/outputs; inspect a failed run before selecting a new one')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    for name, expected in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != expected: raise ValueError('Source seal changed')
    output.mkdir(parents=True); state.mkdir(parents=True); os.chown(state, 10001, 10001)
    cache = Path('/srv/iot-fast/models/iot-rag-embeddings')
    if not cache.exists(): cache.mkdir(parents=True); os.chown(cache, 10001, 10001)
    (output / 'source.json').write_text(json.dumps(seal, indent=2)+'\n')
    (output / 'budget.json').write_text(json.dumps({'remaining_session_seconds': remaining, 'available_memory_bytes': memory, 'ssd_free_bytes': shutil.disk_usage('/srv/iot-fast').free}, indent=2)+'\n')

    def command(label, values, timeout=900, check=True):
        """Capture bounded command consoles without replacing any earlier output."""
        result = subprocess.run(values, cwd=source, capture_output=True, timeout=timeout)
        (output / (label + '.stdout')).write_bytes(result.stdout); (output / (label + '.stderr')).write_bytes(result.stderr)
        (output / (label + '.exit')).write_text(str(result.returncode)+'\n')
        if check and result.returncode: raise RuntimeError(label + ' failed; inspect the retained console/container')
        return result

    dependency = b''.join((source / name).read_bytes() for name in ['infra/rag/Dockerfile', 'infra/rag/requirements.txt'])
    dependency_hash = sha256(dependency).hexdigest(); image = 'iot-coursework-rag:deps-' + dependency_hash[:12]
    present = command('image-before', ['docker', 'image', 'inspect', image], check=False)
    try:
        if present.returncode:
            command('build', ['docker', 'build', '--label', 'iot.dependencies.sha256=' + dependency_hash,
                             '-f', str(source / 'infra/rag/Dockerfile'), '-t', image, str(source)], timeout=1500)
        inspected = json.loads(command('image-after', ['docker', 'image', 'inspect', image]).stdout)[0]
        if inspected['Config']['Labels'].get('iot.dependencies.sha256') != dependency_hash: raise ValueError('Existing dependency image has a different source binding')
        command('pip-freeze', ['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cpus=1', '--memory=1g', image, '-m', 'pip', 'freeze'])
        command('pip-check', ['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cpus=1', '--memory=1g', image, '-m', 'pip', 'check'])
        for stage in ['fetch', 'chunk', 'embed', 'store', 'validate']:
            container = 'iot-' + args.run + '-' + stage
            values = ['docker', 'run', '--name', container, '--init', '--read-only', '--cpus=2', '--memory=4g', '--memory-swap=4g',
                      '--pids-limit=256', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--tmpfs', '/tmp:rw,nosuid,size=512m',
                      '--mount', f'type=bind,source={source},target=/workspace,readonly', '--mount', f'type=bind,source={state},target=/state',
                      '--mount', f'type=bind,source={cache},target=/models', '--entrypoint', '/usr/bin/timeout']
            if stage != 'embed': values += ['--network', 'none', '-e', 'HF_HUB_OFFLINE=1']
            values += [image, '--signal=TERM', '--kill-after=10s', '600', 'python', '-m', 'rag.pipeline', stage, '--source', '/workspace', '--output', '/state']
            command(stage, values, timeout=660, check=False)
            native = json.loads(command(stage + '-container', ['docker', 'inspect', container]).stdout)[0]
            if native['State']['Running']: raise RuntimeError('The bounded container is still running; inspect the same container')
            if native['State']['ExitCode'] != 0 or native['State'].get('OOMKilled'): raise RuntimeError(stage + ' native container failed')
        for name in ['fetch-report.json', 'chunk-report.json', 'embed-report.json', 'store-report.json', 'validate-report.json', 'store-readback.json', 'retrieval.json']:
            shutil.copyfile(state / name, output / name)
        report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'run': args.run, 'image_id': inspected['Id'],
                  'dependency_source_sha256': dependency_hash, 'source_base_commit': seal['base_commit'], 'stages': ['fetch', 'chunk', 'embed', 'store', 'validate'],
                  'scope': 'Real CPU embeddings, isolated native Feast/Milvus storage/replay and semantic retrieval. Not Airflow scheduling, DataHub lineage, MCP or agent integration proof.'}
        (output / 'verification.json').write_text(json.dumps(report, indent=2)+'\n'); print(json.dumps(report, indent=2))
    except Exception as error:
        (output / 'failure.json').write_text(json.dumps({'type': type(error).__name__, 'message': str(error), 'checked_utc': datetime.now(timezone.utc).isoformat()}, indent=2)+'\n')
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True); parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True); parser.add_argument('--run', required=True)
    execute(parser.parse_args())
