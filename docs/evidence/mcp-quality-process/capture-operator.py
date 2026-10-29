"""Preserve actual journal invocation evidence after systemd garbage-collects a completed transient unit."""
from pathlib import Path
from hashlib import sha256
import base64
import json
import subprocess
import tarfile
root = Path('/srv/iot-data/workspace/iot-maintenance-platform')
base = root / 'outputs/mcp-quality-process-29a'; assert not base.exists()
raw = subprocess.check_output(['journalctl', '-u', 'iot-mcp-quality-29a', '--no-pager', '-o', 'json'])
private = []
for name in ['agent-platform.json', 'mcp-tools.json']:
    for value in json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values():
        private.extend([value.encode(), base64.b64encode(value.encode()), base64.b64encode(('Bearer ' + value).encode())])
assert not any(value in raw for value in private)
rows = [json.loads(line) for line in raw.splitlines()]
invocation = '36be62af556f49e4b0d9a2d3ca9ea32a'
messages = [r['MESSAGE'] for r in rows if r.get('_SYSTEMD_INVOCATION_ID') == invocation]
assert json.loads('\n'.join(messages)) == json.loads((root / 'outputs/mcp-quality-29a/verification.json').read_bytes())
assert any('Deactivated successfully' in row.get('MESSAGE', '') for row in rows)
base.mkdir(); (base / 'journal.jsonl').write_bytes(raw); (base / 'capture-operator.py').write_bytes(Path(__file__).read_bytes())
(base / 'capture.json').write_text(json.dumps({'passed': True, 'credential_scan_passed': True,
    'invocation_id': invocation, 'journal_sha256': sha256(raw).hexdigest(),
    'scope': 'Original systemd journal and exact native completion report; no inferred invocation from empty inactive-unit properties.'}, indent=2) + '\n')
manifest = {'files': {p.name: {'bytes': p.stat().st_size, 'sha256': sha256(p.read_bytes()).hexdigest()} for p in sorted(base.iterdir())}}
body = (json.dumps(manifest, indent=2) + '\n').encode(); (base / 'manifest.json').write_bytes(body)
archive = root / 'outputs/mcp-quality-process-29a.tar'; assert not archive.exists()
with tarfile.open(archive, 'w') as package:
    for path in sorted(base.iterdir()): package.add(path, arcname='mcp-quality-process/' + path.name)
print(json.dumps({'files': len(manifest['files']), 'manifest_sha256': sha256(body).hexdigest(),
    'archive_bytes': archive.stat().st_size, 'archive_sha256': sha256(archive.read_bytes()).hexdigest()}))
