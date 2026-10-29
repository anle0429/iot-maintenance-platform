"""Read source-pinned integration receipts without starting services or importing model dependencies."""
from hashlib import sha256
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def read(base, name):
    """Decode an original captured JSON payload."""
    return json.loads((base / name).read_bytes())


def capsule(name, expected):
    """Pin the inventory itself and every declared byte, rejecting escaped or unrecorded files."""
    base = ROOT / 'docs/evidence' / name
    raw = (base / 'manifest.json').read_bytes()
    assert sha256(raw).hexdigest() == expected, 'Integration inventory changed'
    manifest = json.loads(raw)
    actual = {path.relative_to(base).as_posix() for path in base.rglob('*') if path.is_file()}
    assert actual == set(manifest['files']) | {'manifest.json'}
    for name, row in manifest['files'].items():
        path = (base / name).resolve()
        assert path.is_relative_to(base.resolve())
        payload = path.read_bytes()
        assert len(payload) == row['bytes'] and sha256(payload).hexdigest() == row['sha256'], name
    return base, len(manifest['files'])


def sources(base, prefix, current=False):
    """Bind each native run to its exact uploaded source, optionally also requiring current repository equality."""
    seal = read(base, prefix + '/source/manifest.json')
    assert seal['files']
    for name, digest in seal['files'].items():
        snapshot = (base / prefix / 'source/tree' / name).resolve()
        assert snapshot.is_relative_to((base / prefix / 'source/tree').resolve())
        assert sha256(snapshot.read_bytes()).hexdigest() == digest, name
        if current:
            path = (ROOT / name).resolve()
            assert path.is_relative_to(ROOT) and sha256(path.read_bytes()).hexdigest() == digest, name
    return seal


def exited(container, code=0):
    """Require an actual stopped native process and its observed exit/OOM state."""
    state = container['State']
    assert not state['Running'] and state['ExitCode'] == code and not state['OOMKilled']
    assert state['FinishedAt'] != '0001-01-01T00:00:00Z'
