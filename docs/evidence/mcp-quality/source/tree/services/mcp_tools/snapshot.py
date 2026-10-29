"""Copy only the declared sealed knowledge files into a per-replica writable Milvus directory."""
from hashlib import sha256
from pathlib import Path
import json
import shutil

NAMES = {'knowledge.db','registry.db','chunks.parquet','chunks.json','vectors.json','documents.json'}


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


if __name__ == '__main__':
    prepare('/snapshot','/state','/snapshot-seal/seal.json')
