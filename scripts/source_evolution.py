"""Authenticate exact reviewed source evolution before checking earlier immutable runtime receipts."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.git_sources import git_blobs, historical_blob, git_environment
MANIFEST_SHA256 = 'ae5e11114639d5b5ddc8930318202f89dba9472b1b48efe88d61a520be8ee5f3'


@lru_cache(maxsize=1)
def revision_blobs(before, after, names):
    """Cache only immutable Git objects; every current working byte is checked on each use."""
    return git_blobs(ROOT, before, names), git_blobs(ROOT, after, names)


def recorded_source(name, current=None):
    """Return the previous checkpoint only after authenticating the exact reviewed replacement."""
    path = (ROOT / name).resolve()
    if not path.is_relative_to(ROOT): raise ValueError('Historical source escaped the repository')
    current = path.read_bytes() if current is None else current
    raw = (ROOT / 'docs/source_evolution.json').read_bytes().replace(b'\r\n', b'\n')
    if sha256(raw).hexdigest() != MANIFEST_SHA256: raise ValueError('Reviewed source transition changed')
    transition = json.loads(raw)
    if transition['format'] != 'iot-reviewed-source-evolution-v1': raise ValueError('Unknown source transition')
    if name not in transition['files']: return current
    row = transition['files'][name]
    names = tuple(n for n, metadata in transition['files'].items() if 'after_blob' not in metadata)
    before, after = revision_blobs(transition['before_commit'], transition['after_commit'], names)
    original = before.get(name); replacement = after.get(name)
    if 'after_blob' in row:
        original = historical_blob(ROOT, transition['before_commit'] + ':' + name)
        replacement = subprocess.check_output(['git', 'cat-file', 'blob', row['after_blob']], cwd=ROOT,
                                               env=git_environment(), timeout=30)
    if (sha256(original).hexdigest() != row['before_sha256']
            or sha256(replacement).hexdigest() != row['after_sha256']
            or current.replace(b'\r\n', b'\n') != replacement):
        raise ValueError('Unreviewed current or historical source: ' + name)
    return original


def validate_recorded_seal(seal, commit):
    """Check all earlier source bytes without invoking the current deployment admission contract."""
    if seal.get('format') != 'iot-sealed-llm-ci-v1' or seal.get('commit') != commit or not seal.get('files'):
        raise ValueError('Historical CI seal has a different revision or format')
    for name, digest in seal['files'].items():
        if sha256(recorded_source(name)).hexdigest() != digest:
            raise ValueError('Recorded CI source differs: ' + name)
    return sha256(json.dumps(seal, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def recorded_required_inputs(seal):
    """Apply the preceding source declaration to an earlier CI receipt rather than new release inputs."""
    name = 'infra/ci/agents-source.json'; config = json.loads(recorded_source(name))
    required = {name, *config['required'], *config['quality_manifests']}
    for relative in config['quality_manifests']: required.update(json.loads(recorded_source(relative))['files'])
    if not required <= seal['files'].keys(): raise ValueError('Historical CI dependency is missing')
    return len(required)


def recorded_quality_sources():
    """Keep the original strict quality-cache comparison scoped to the historical source checkpoint."""
    result = {}
    for name in ['mcp-quality', 'mcp-quality-native']:
        raw = recorded_source('docs/evidence/' + name + '/source/manifest.json'); seal = json.loads(raw)
        for relative, digest in seal['files'].items():
            if sha256(recorded_source(relative)).hexdigest() != digest: raise ValueError('Historical quality source changed')
        result[name] = {'files': len(seal['files']), 'manifest_sha256': sha256(raw).hexdigest()}
    return result
