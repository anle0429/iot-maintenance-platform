"""Validate a path-only history rewrite using original commit/tree Merkle proofs without removed file bodies."""
from hashlib import sha1, sha256
from io import BytesIO
from pathlib import PurePosixPath
import json, subprocess, tarfile
try:
    from .git_sources import history_manifest, git_environment
except ImportError:
    from git_sources import history_manifest, git_environment


def object_id(kind, body):
    """Recompute Git's content identity for an original commit, tree or retained current object."""
    return sha1(kind.encode()+b' '+str(len(body)).encode()+b'\0'+body).hexdigest()


def tree_entries(body):
    """Parse exact binary tree entries, preserving file modes, UTF-8 names and child object identities."""
    entries = []; offset = 0
    while offset < len(body):
        space = body.index(b' ', offset); nul = body.index(b'\0', space)
        mode = body[offset:space]; name = body[space+1:nul]; identity = body[nul+1:nul+21]
        if len(identity) != 20 or not name or b'/' in name: raise ValueError('Malformed original Git tree')
        entries.append((mode, name, identity.hex())); offset = nul+21
    if offset != len(body): raise ValueError('Incomplete original Git tree')
    return entries


def filtered_tree(identity, objects, removed, prefix=''):
    """Remove only the declared literal paths; all retained names, modes and blob/tree identities stay exact."""
    raw = b''
    for mode, name, child in tree_entries(objects[(identity, 'tree')]):
        path = prefix+name.decode('utf-8')
        if path in removed: continue
        if mode == b'40000' and any(value.startswith(path+'/') for value in removed):
            child = filtered_tree(child, objects, removed, path+'/')
            if child is None: continue
        raw += mode+b' '+name+b'\0'+bytes.fromhex(child)
    return object_id('tree', raw) if raw or not prefix else None


def rewritten_commit(body, mapping, objects, removed):
    """Change only the root tree and mapped parents; preserve every author/date/encoding/message byte."""
    header, separator, message = body.partition(b'\n\n')
    if not separator: raise ValueError('Malformed original commit')
    lines = header.split(b'\n')
    if not lines[0].startswith(b'tree '): raise ValueError('Original commit has no root tree')
    lines[0] = b'tree '+filtered_tree(lines[0][5:].decode(), objects, removed).encode()
    for i, line in enumerate(lines):
        if line.startswith(b'parent '): lines[i] = b'parent '+mapping[line[7:].decode()].encode()
    return b'\n'.join(lines)+separator+message


def original_objects(root, manifest):
    """Authenticate original metadata/tree proof bytes; reject file/blob bodies, links, extras and corrupt objects."""
    raw = (root/'docs/history-rewrite'/manifest['archive']).read_bytes()
    if len(raw) != manifest['archive_bytes'] or sha256(raw).hexdigest() != manifest['archive_sha256']:
        raise ValueError('History proof archive changed')
    found = {}; seen = set()
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if not member.isfile() or path.is_absolute() or '..' in path.parts or member.name not in manifest['original_objects'] or member.name in seen:
                raise ValueError('Unexpected history proof member')
            seen.add(member.name); pin = manifest['original_objects'][member.name]
            if member.size != pin['bytes'] or member.size > 1_000_000: raise ValueError('Invalid history proof size')
            body = archive.extractfile(member).read(); identity, kind = path.name.split('.')
            if kind not in {'commit', 'tree'} or len(body) != pin['bytes'] or sha256(body).hexdigest() != pin['sha256'] or object_id(kind, body) != identity:
                raise ValueError('Original Git object identity changed')
            found[(identity, kind)] = body
    if seen != set(manifest['original_objects']): raise ValueError('Original proof objects are missing')
    return found


def verify_history(root):
    """Reconstruct every mapped commit and verify the actual filtered graph and complete path removal."""
    manifest = history_manifest(root); mapping = manifest['commits']; removed = set(manifest['removed_paths'])
    objects = original_objects(root, manifest)
    requested = list(mapping.values())
    result = subprocess.run(['git', 'cat-file', '--batch'], cwd=root, input=('\n'.join(requested)+'\n').encode(),
                            capture_output=True, check=True, env=git_environment(), timeout=60)
    stream = BytesIO(result.stdout)
    for original, actual in mapping.items():
        header = stream.readline().decode().split(); assert header[0] == actual and header[1] == 'commit'
        body = stream.read(int(header[2])); assert stream.read(1) == b'\n'
        expected = rewritten_commit(objects[(original, 'commit')], mapping, objects, removed)
        assert body == expected and object_id('commit', body) == actual
    assert not stream.read()
    changed = [old for old, new in mapping.items() if old != new]
    absent = subprocess.run(['git', 'cat-file', '--batch-check'], cwd=root, input=('\n'.join(changed)+'\n').encode(),
                            capture_output=True, check=True, env=git_environment(), timeout=60).stdout.decode().splitlines()
    assert absent == [old+' missing' for old in changed], 'Original changed commits must not remain in the filtered object database'
    assert not subprocess.check_output(['git', 'for-each-ref', '--format=%(refname)', 'refs/replace'], cwd=root, env=git_environment())
    assert not subprocess.check_output(['git', 'log', '--all', '--format=%H', '--', *sorted(removed)], cwd=root, env=git_environment())
    count = int(subprocess.check_output(['git', 'rev-list', '--count', manifest['filtered_main']], cwd=root, env=git_environment()))
    assert count == manifest['original_commit_count'] == manifest['filtered_commit_count'] == len(mapping)
    return {'passed': True, 'original_commits': len(mapping), 'filtered_commits': count, 'removed_paths': len(removed),
            'changed_commit_objects_absent': len(changed), 'original_metadata_tree_objects': len(objects),
            'retained_paths_modes_and_blob_ids_unchanged': True, 'authors_dates_messages_preserved': True,
            'removed_file_bodies_in_proof': False,
            'scope': 'Path-only Git history migration and source provenance; original native measurements are unchanged. Existing external clones/caches are outside this local verification.'}
