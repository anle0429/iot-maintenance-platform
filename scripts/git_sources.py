"""Read exact historical blobs through an authenticated original-to-filtered commit identity map."""
from hashlib import sha1, sha256
from io import BytesIO
from functools import lru_cache
from pathlib import Path, PurePosixPath
import json, os, re, subprocess

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HISTORY_MANIFEST_SHA256 = '710e3af80a77012b1a84868f33f0ec4145396c51ff68f095358477999b582ec5'


def history_manifest(root):
    """Pin the migration inventory; isolated generic Git fixtures may have no project migration."""
    root = Path(root).resolve(); path = root/'docs/history-rewrite/manifest.json'
    if not path.exists():
        if root == PROJECT_ROOT: raise ValueError('The pinned history migration inventory is missing')
        return None
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != HISTORY_MANIFEST_SHA256: raise ValueError('History migration inventory changed')
    value = json.loads(raw); mapping = value.get('commits', {})
    if (value.get('format') != 'iot-filtered-history-v1' or not mapping
            or len(mapping) != value['original_commit_count'] or len(set(mapping.values())) != len(mapping)
            or any(not re.fullmatch('[0-9a-f]{40}', old) or not re.fullmatch('[0-9a-f]{40}', new) for old, new in mapping.items())):
        raise ValueError('Invalid original-to-filtered commit identities')
    return value


def git_environment():
    """Disable local replace refs so an evidence read uses actual objects rather than a mutable replacement."""
    return dict(os.environ, GIT_NO_REPLACE_OBJECTS='1', GIT_OPTIONAL_LOCKS='0')


@lru_cache(maxsize=1024)
def require_commit(root, identity):
    """Cache only the type check of an immutable object ID, never a branch or mutable migration manifest."""
    result = subprocess.run(['git', 'cat-file', '-t', identity], cwd=Path(root), capture_output=True,
                            env=git_environment(), timeout=30)
    if result.returncode or result.stdout.strip() != b'commit': raise ValueError('The historical commit is unavailable: '+identity)


def resolve_commit(root, commit):
    """Resolve a pinned original identity without changing original receipts or accepting a moving branch."""
    if not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Pin a full original or current commit ID')
    manifest = history_manifest(root); actual = manifest['commits'].get(commit, commit) if manifest else commit
    require_commit(str(Path(root).resolve()), actual)
    return actual


def resolve_revision(root, revision):
    """Resolve a packaging CLI revision to the actual filtered commit; new packets record that actual identity."""
    if re.fullmatch('[0-9a-f]{40}', revision): return resolve_commit(root, revision)
    raw = subprocess.check_output(['git', 'rev-parse', '--verify', revision+'^{commit}'], cwd=Path(root),
                                  env=git_environment(), timeout=30).decode().strip()
    return resolve_commit(root, raw)


def historical_blob(root, specification):
    """Read one explicitly pinned commit:path through the same binary-safe multi-blob reader."""
    commit, separator, name = specification.partition(':')
    if not separator: raise ValueError('Use a pinned commit:path source specification')
    return git_blobs(root, commit, [name])[name]


def rewrite_project_links(body, root=PROJECT_ROOT):
    """Translate only this repository's historical GitHub URL identities; preserve every other byte."""
    manifest = history_manifest(root)
    if not manifest: return body
    mapping = manifest['commits']
    pattern = rb'(https://github\.com/anle0429/iot-maintenance-platform/(?:blob|tree)/)([0-9a-f]{7,40})(/)'
    def replace(match):
        """Accept only a unique known original prefix, leaving external and current links untouched."""
        old = match[2].decode(); candidates = [key for key in mapping if key.startswith(old)]
        if len(candidates) > 1: raise ValueError('Ambiguous original commit URL')
        return match[1]+mapping[candidates[0]].encode()+match[3] if candidates else match[0]
    return re.sub(pattern, replace, body)


def git_blobs(root, commit, names):
    """Check original selection, filtered-object framing and content identities without reading working files."""
    names = list(names)
    if not re.fullmatch(r'[0-9a-f]{40}', commit) or not names or len(set(names)) != len(names):
        raise ValueError('Pin one full commit and unique source paths')
    for name in names:
        path = PurePosixPath(name)
        if not name or path.is_absolute() or '..' in path.parts or any(v in name for v in ['\0', '\r', '\n']):
            raise ValueError('Use unambiguous repository-relative source paths')
    actual = resolve_commit(root, commit)
    request = ''.join(actual+':'+name+'\n' for name in names).encode('utf-8')
    result = subprocess.run(['git', 'cat-file', '--batch'], cwd=Path(root), input=request,
                            capture_output=True, timeout=120, check=True, env=git_environment())
    stream = BytesIO(result.stdout); blobs = {}
    for name in names:
        header = stream.readline(256).rstrip(b'\n').split(b' ')
        if len(header) != 3 or header[1] != b'blob' or not re.fullmatch(b'[0-9a-f]{40}', header[0]) or not header[2].isdigit():
            raise ValueError('A pinned source blob is missing or malformed: '+name)
        size = int(header[2]); raw = stream.read(size)
        if len(raw) != size or stream.read(1) != b'\n': raise ValueError('Git source framing was incomplete')
        identity = sha1(b'blob '+str(size).encode()+b'\0'+raw).hexdigest().encode()
        if identity != header[0]: raise ValueError('Git source content identity differs')
        blobs[name] = raw
    if stream.read(): raise ValueError('Unexpected extra Git source output')
    return blobs
