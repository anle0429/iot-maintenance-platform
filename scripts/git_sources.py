"""Read exact historical source blobs with one binary-framed Git process per pinned manifest."""
from hashlib import sha1
from io import BytesIO
from pathlib import Path, PurePosixPath
import re
import subprocess


def git_blobs(root, commit, names):
    """Check committed object framing and content identities without consulting or changing working files."""
    names = list(names)
    if not re.fullmatch(r'[0-9a-f]{40}', commit) or not names or len(set(names)) != len(names):
        raise ValueError('Pin one full commit and unique source paths')
    for name in names:
        path = PurePosixPath(name)
        if not name or path.is_absolute() or '..' in path.parts or any(v in name for v in ['\0', '\r', '\n']):
            raise ValueError('Use unambiguous repository-relative source paths')
    request = ''.join(commit+':'+name+'\n' for name in names).encode('utf-8')
    result = subprocess.run(['git', 'cat-file', '--batch'], cwd=Path(root), input=request,
                            capture_output=True, timeout=120, check=True)
    stream = BytesIO(result.stdout); blobs = {}
    for name in names:
        header = stream.readline(256).rstrip(b'\n').split(b' ')
        if len(header) != 3 or header[1] != b'blob' or not re.fullmatch(b'[0-9a-f]{40}', header[0]) or not header[2].isdigit():
            raise ValueError('A pinned source blob is missing or malformed: '+name)
        size = int(header[2]); raw = stream.read(size)
        if len(raw) != size or stream.read(1) != b'\n':
            raise ValueError('Git source framing was incomplete')
        identity = sha1(b'blob '+str(size).encode()+b'\0'+raw).hexdigest().encode()
        if identity != header[0]: raise ValueError('Git source content identity differs')
        blobs[name] = raw
    if stream.read(): raise ValueError('Unexpected extra Git source output')
    return blobs
