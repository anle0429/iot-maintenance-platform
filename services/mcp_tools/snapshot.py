"""Copy only the declared sealed knowledge files into a per-replica writable Milvus directory."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import shutil

NAMES = {'knowledge.db','registry.db','chunks.parquet','chunks.json','vectors.json','documents.json'}
RUNTIME_STATE = Path('/state/snapshot')


def prepare(source, target, seal):
    """Refuse changed/escaped snapshots or an existing destination before opening native database files."""
    source = Path(source); target = Path(target)
    manifest = json.loads(Path(seal).read_bytes())
    if set(manifest['files']) != NAMES: raise ValueError('The knowledge snapshot inventory differs')
    if any(target.iterdir()): raise ValueError('Keep previous replica state intact')
    for name in sorted(NAMES):
        original = source/name
        if original.is_symlink() or sha256(original.read_bytes()).hexdigest() != manifest['files'][name]:
            raise ValueError('The native snapshot changed')
        with (target/name).open('xb') as copied, original.open('rb') as incoming: shutil.copyfileobj(incoming,copied)


def prepare_runtime(source, root, seal):
    """Rebuild a private replica's writable state from the immutable source on each Pod init."""
    root = Path(root)
    current = root / RUNTIME_STATE.name
    if current.is_symlink() or (current.exists() and not current.is_dir()):
        raise ValueError('The replica snapshot location is not a directory')
    with TemporaryDirectory(dir=root, prefix='.snapshot-') as directory:
        staging = Path(directory) / 'next'
        staging.mkdir()
        prepare(source, staging, seal)
        if current.exists():
            current.rename(Path(directory) / 'previous')
        staging.rename(current)


if __name__ == '__main__':
    prepare_runtime('/snapshot', RUNTIME_STATE.parent, '/snapshot-seal/seal.json')
