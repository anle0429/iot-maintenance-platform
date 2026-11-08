"""Authenticate exact reviewed source evolution before checking earlier immutable runtime receipts."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import json
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.git_sources import git_blobs, git_environment, resolve_commit
MANIFEST_SHA256 = 'ae5e11114639d5b5ddc8930318202f89dba9472b1b48efe88d61a520be8ee5f3'
CHECKPOINTS_SHA256 = 'ccb5da15f49bbd309d0e3644c7f8bf537102d900f5176bb6c793fb0b3317e2b3'
CHECKPOINT_COMMITS = ('888f7a57c1cd815967ba12e2244687116cbffe99',
                      'f12ea06da2e039d139638c154470f896ed23a7cf',
                      '9471bfe0b2adf3230ea88835df033062fd77a803',
                      'ad2da29262e3088c942a9240d7157708c094aca4',
                      '1e883f4c68ea27f973f9191f4e187e0438a9437c')


@lru_cache(maxsize=1)
def revision_blobs(before, after, names):
    """Cache only immutable Git objects; every current working byte is checked on each use."""
    return git_blobs(ROOT, before, names), git_blobs(ROOT, after, names)


def reviewed_inventories():
    """Keep the frozen legacy inventory intact and authenticate the separate checkpoint inventory."""
    raw = (ROOT / 'docs/source_evolution.json').read_bytes().replace(b'\r\n', b'\n')
    if sha256(raw).hexdigest() != MANIFEST_SHA256: raise ValueError('Reviewed source transition changed')
    transition = json.loads(raw)
    if transition['format'] != 'iot-reviewed-source-evolution-v1': raise ValueError('Unknown source transition')
    raw = (ROOT / 'docs/source_checkpoints.json').read_bytes().replace(b'\r\n', b'\n')
    if sha256(raw).hexdigest() != CHECKPOINTS_SHA256: raise ValueError('Reviewed source checkpoints changed')
    inventory = json.loads(raw)
    if (inventory.get('format') != 'iot-reviewed-source-checkpoints-v3'
            or inventory.get('legacy_manifest_sha256') != MANIFEST_SHA256
            or tuple(inventory.get(key) for key in ('legacy_commit', 'previous_commit', 'accepted_commit', 'corrected_ci_commit', 'current_commit')) != CHECKPOINT_COMMITS
            or (transition['before_commit'], transition['after_commit']) != CHECKPOINT_COMMITS[:2]
            or not isinstance(inventory.get('files'), dict)
            or not set(transition['files']) <= inventory['files'].keys()):
        raise ValueError('Unknown or incomplete source checkpoint inventory')
    for name, row in inventory['files'].items():
        path = (ROOT / name).resolve()
        pins = row.get('sha256', {})
        if (not path.is_relative_to(ROOT) or set(pins) != set(CHECKPOINT_COMMITS)
                or any(value is not None and (not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value))
                       for value in pins.values()) or pins[CHECKPOINT_COMMITS[-1]] is None):
            raise ValueError('Malformed exact checkpoint source pin')
        adapter = transition['files'].get(name)
        if (adapter and row.get('legacy_adapter_sha256') != adapter['after_sha256']
                or not adapter and 'legacy_adapter_sha256' in row):
            raise ValueError('Historical source adapter changed')
    return transition, inventory


@lru_cache(maxsize=8)
def checkpoint_blobs(commit, names):
    """Cache only authenticated immutable checkpoint blobs, never working files or inventories."""
    return git_blobs(ROOT, commit, names) if names else {}


@lru_cache(maxsize=8)
def require_absent(commit, names):
    """Authenticate declared source absence against the actual immutable Git tree."""
    if not names: return
    actual = resolve_commit(ROOT, commit)
    found = subprocess.check_output(['git', 'ls-tree', '-rz', '--name-only', actual, '--', *names],
                                    cwd=ROOT, env=git_environment(), timeout=30)
    if found: raise ValueError('Historical source declared absent exists in Git')


@lru_cache(maxsize=8)
def adapter_blob(identity):
    """Read an immutable legacy adapter object while disabling mutable Git replacement references."""
    return subprocess.check_output(['git', 'cat-file', 'blob', identity], cwd=ROOT,
                                   env=git_environment(), timeout=30)


def authenticated_views(transition, inventory):
    """Check every immutable source pin and the original adapter without substituting a logical checkpoint."""
    names = tuple(n for n, metadata in transition['files'].items() if 'after_blob' not in metadata)
    before, after = revision_blobs(transition['before_commit'], transition['after_commit'], names)
    for name, row in transition['files'].items():
        original, replacement = before.get(name), after.get(name)
        if 'after_blob' in row:
            original = checkpoint_blobs(transition['before_commit'], (name,))[name]
            replacement = adapter_blob(row['after_blob'])
        if (sha256(original).hexdigest() != row['before_sha256']
                or sha256(replacement).hexdigest() != row['after_sha256']
                or inventory['files'][name]['sha256'][CHECKPOINT_COMMITS[0]] != row['before_sha256']):
            raise ValueError('Unreviewed immutable legacy source: ' + name)
    views = {}
    for checkpoint in CHECKPOINT_COMMITS:
        present = tuple(name for name, row in inventory['files'].items() if row['sha256'][checkpoint] is not None)
        absent = tuple(name for name, row in inventory['files'].items() if row['sha256'][checkpoint] is None)
        views[checkpoint] = checkpoint_blobs(checkpoint, present)
        require_absent(checkpoint, absent)
        for name in present:
            if sha256(views[checkpoint][name]).hexdigest() != inventory['files'][name]['sha256'][checkpoint]:
                raise ValueError('Unreviewed immutable checkpoint source: ' + name)
    return views


def recorded_source(name, current=None, *, checkpoint=None):
    """Authenticate latest mapped bytes, then return the exact888 default or one explicit reviewed source view."""
    path = (ROOT / name).resolve()
    if not path.is_relative_to(ROOT): raise ValueError('Historical source escaped the repository')
    current = path.read_bytes() if current is None else current
    transition, inventory = reviewed_inventories()
    checkpoint = CHECKPOINT_COMMITS[0] if checkpoint is None else checkpoint
    if checkpoint not in CHECKPOINT_COMMITS: raise ValueError('Use one exact reviewed source checkpoint')
    if name not in inventory['files']: return current
    views = authenticated_views(transition, inventory)
    if current.replace(b'\r\n', b'\n') != views[CHECKPOINT_COMMITS[-1]][name]:
        raise ValueError('Unreviewed current source: ' + name)
    if name not in views[checkpoint]: raise ValueError('Source did not exist at the selected checkpoint: ' + name)
    return views[checkpoint][name]


def validate_recorded_seal(seal, commit, *, checkpoint=None):
    """Check all earlier source bytes without invoking the current deployment admission contract."""
    if seal.get('format') != 'iot-sealed-llm-ci-v1' or seal.get('commit') != commit or not seal.get('files'):
        raise ValueError('Historical CI seal has a different revision or format')
    for name, digest in seal['files'].items():
        if sha256(recorded_source(name, checkpoint=checkpoint)).hexdigest() != digest:
            raise ValueError('Recorded CI source differs: ' + name)
    return sha256(json.dumps(seal, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def recorded_required_inputs(seal, *, checkpoint=None):
    """Apply the preceding source declaration to an earlier CI receipt rather than new release inputs."""
    name = 'infra/ci/agents-source.json'; config = json.loads(recorded_source(name, checkpoint=checkpoint))
    required = {name, *config['required'], *config['quality_manifests']}
    for relative in config['quality_manifests']: required.update(json.loads(recorded_source(relative, checkpoint=checkpoint))['files'])
    if not required <= seal['files'].keys(): raise ValueError('Historical CI dependency is missing')
    return len(required)


def recorded_quality_sources(*, checkpoint=None):
    """Keep the original strict quality-cache comparison scoped to the historical source checkpoint."""
    result = {}
    for name in ['mcp-quality', 'mcp-quality-native']:
        raw = recorded_source('docs/evidence/' + name + '/source/manifest.json', checkpoint=checkpoint); seal = json.loads(raw)
        for relative, digest in seal['files'].items():
            if sha256(recorded_source(relative, checkpoint=checkpoint)).hexdigest() != digest: raise ValueError('Historical quality source changed')
        result[name] = {'files': len(seal['files']), 'manifest_sha256': sha256(raw).hexdigest()}
    return result
