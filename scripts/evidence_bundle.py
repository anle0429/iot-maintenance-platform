"""Restore byte-pinned historical receipts locally while keeping the submission tree small and browsable."""
from hashlib import sha256
from pathlib import Path, PurePosixPath
import json, re, tarfile

ROOT = Path(__file__).resolve().parents[1]
INDEX_SHA256 = 'a9765d6b93368d3e9665a0349f69a20ceb30c58d9c753be6b668e9a23b06ea82'


def safe_name(name):
    """Restrict archive members to regular evidence paths on both Windows and POSIX."""
    path = PurePosixPath(name)
    if (not isinstance(name, str) or not name.startswith('docs/evidence/') or path.is_absolute()
            or len(path.parts) < 4 or str(path) != name or '..' in path.parts
            or any(v in name for v in ['\\', ':', '\0', '\r', '\n'])
            or any(part.endswith((' ', '.')) for part in path.parts)):
        raise ValueError('Unsafe evidence member')
    return path


def checked_index(index):
    """Require bounded, unique, case-safe payload names and explicit original SHA-256 values."""
    if index.get('format') != 'iot-recorded-evidence-bundle-v1' or not index.get('files'):
        raise ValueError('Unexpected evidence bundle format')
    names = set(); total = 0
    for name, pin in index['files'].items():
        safe_name(name)
        if name.casefold() in names: raise ValueError('Evidence names collide on Windows')
        names.add(name.casefold())
        if type(pin.get('bytes')) is not int or not 0 <= pin['bytes'] <= 10_000_000 or not re.fullmatch('[0-9a-f]{64}', pin.get('sha256', '')):
            raise ValueError('Invalid evidence size or hash')
        total += pin['bytes']
    if total != index['original_payload_bytes'] or total > 200_000_000 or len(names) > 10_000:
        raise ValueError('Evidence bundle exceeds its declared bounds')
    return index['files']


def safe_target(root, name):
    """Reject symlinks, Windows junctions and escaped parents before creating any local receipt."""
    target = root.joinpath(*safe_name(name).parts)
    for path in [target, *target.parents]:
        if path == root: break
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('Do not restore through a linked evidence path')
    if not target.resolve().is_relative_to(root): raise ValueError('Evidence target escaped the repository')
    return target


def checked_members(archive, files):
    """Read every original byte, rejecting links, duplicates, omitted members and hash/length mismatches."""
    seen = set()
    with tarfile.open(archive, 'r:gz') as source:
        for member in source:
            safe_name(member.name)
            if not member.isfile() or member.name in seen or member.name not in files:
                raise ValueError('Unexpected evidence archive member')
            seen.add(member.name); pin = files[member.name]
            if member.size != pin['bytes']: raise ValueError('Evidence member size changed')
            body = source.extractfile(member).read()
            if len(body) != pin['bytes'] or sha256(body).hexdigest() != pin['sha256']:
                raise ValueError('Evidence payload hash changed')
            yield member.name, body
    if seen != set(files): raise ValueError('Evidence members are missing')


def restore(root, archive, index, *, verify_only=False):
    """Validate the entire archive and existing files before restoring only missing receipts; never overwrite a conflict."""
    root = Path(root).resolve(); archive = Path(archive); files = checked_index(index)
    raw = archive.read_bytes()
    if len(raw) != index['archive_bytes'] or sha256(raw).hexdigest() != index['archive_sha256']:
        raise ValueError('Evidence archive bytes changed')
    missing = []
    for name, pin in files.items():
        target = safe_target(root, name)
        if target.exists():
            if not target.is_file() or target.stat().st_size != pin['bytes'] or sha256(target.read_bytes()).hexdigest() != pin['sha256']:
                raise ValueError('Preserve and investigate changed local evidence: '+name)
        else: missing.append(name)
    for _name, _body in checked_members(archive, files): pass
    restored = 0
    if not verify_only and missing:
        selected = set(missing)
        for name, body in checked_members(archive, files):
            if name not in selected: continue
            target = safe_target(root, name); target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with target.open('xb') as stream: stream.write(body)
                restored += 1
            except FileExistsError:
                if target.read_bytes() != body: raise ValueError('Concurrent evidence conflict: '+name)
    return {'passed': True, 'bundle_payloads': len(files), 'original_payload_bytes': index['original_payload_bytes'],
            'restored_missing_files': restored, 'already_present': len(files)-len(missing), 'verify_only': verify_only,
            'scope': 'Original receipt restoration/integrity only; no new pipeline, service, model request or fabricated measurement.'}


def materialize(root=ROOT, *, verify_only=False, verify_sources=False):
    """Use the pinned submission index and original payload archive without trusting a mutable success marker."""
    root = Path(root).resolve(); folder = root/'docs/evidence-archive'; raw = (folder/'manifest.json').read_bytes()
    if sha256(raw).hexdigest() != INDEX_SHA256: raise ValueError('Evidence bundle index changed')
    index = json.loads(raw)
    if index['archive'] != 'recorded-runs.tar.gz': raise ValueError('Unexpected evidence archive name')
    if verify_sources:
        try:
            from .git_sources import git_blobs
        except ImportError:
            from git_sources import git_blobs
        originals = git_blobs(root, index['source_commit'], index['files'])
        for name, pin in index['files'].items():
            if len(originals[name]) != pin['bytes'] or sha256(originals[name]).hexdigest() != pin['sha256']:
                raise ValueError('Archived receipt differs from its original Git blob: '+name)
    report = restore(root, folder/index['archive'], index, verify_only=verify_only)
    report['original_git_bindings_checked'] = len(index['files']) if verify_sources else 0
    return report
