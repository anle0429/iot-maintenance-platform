"""Check exact native capsule payload bytes and historical Git source bindings without executing evidence."""
from pathlib import Path
from hashlib import sha256
import json,subprocess
ROOT=Path(__file__).resolve().parents[1]


def verified(base,count,native_count):
    """Require exactly the manifest-listed safe paths and compare all original byte lengths and digests."""
    read=lambda n:json.loads((base/n).read_bytes())
    manifest=read('manifest.json');files={p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file() and p!=base/'manifest.json'};assert files==set(manifest['files']) and len(files)==count
    for n,v in manifest['files'].items():
        assert not Path(n).is_absolute() and '..' not in Path(n).parts
        raw=(base/n).read_bytes();assert len(raw)==v['bytes'] and sha256(raw).hexdigest()==v['sha256'] and b'PRIVATE KEY-----' not in raw,n
    native=read('native-manifest.json');assert len(native['files'])==native_count and all(manifest['files'][n]==v for n,v in native['files'].items())
    return read


def source_bindings(seal):
    """Verify every declared source/input against its immutable Git blob; return the checked count."""
    for n,h in seal['files'].items():assert sha256(subprocess.check_output(['git','show',seal['commit']+':'+n],cwd=ROOT)).hexdigest()==h,n
    return len(seal['files'])
