"""Capture terminal native MCP release receipts and exact source, refusing actual credential values."""
from pathlib import Path
from hashlib import sha256
import base64
import json
import subprocess
import tarfile
root = Path('/srv/iot-data/workspace/iot-maintenance-platform')
target = root / 'outputs/mcp-quality-capture-29a'
unit = subprocess.check_output(['systemctl', 'show', 'iot-mcp-quality-29a',
    '--property=ActiveState,SubState,Result,ExecMainStatus,InvocationID,ExecStart'])
assert b'ActiveState=inactive\n' in unit and b'ExecMainStatus=0\n' in unit and b'Result=success\n' in unit
assert not target.exists(); target.mkdir()
private = []
for name in ['agent-platform.json', 'mcp-tools.json']:
    for value in json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values():
        private.extend([value.encode(), base64.b64encode(value.encode()), base64.b64encode(('Bearer ' + value).encode())])

def keep(name, body):
    """Keep original bytes only after screening actual secret values and known encodings."""
    assert not any(value in body for value in private), name
    path = target / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)

keep('unit.txt', unit); keep('capture-operator.py', Path(__file__).read_bytes())
native = root / 'outputs/mcp-quality-29a'; exclusions = []
for path in sorted(native.rglob('*')):
    if not path.is_file(): continue
    name = path.relative_to(native).as_posix()
    if any(part.startswith('.') for part in path.relative_to(native).parts):
        exclusions.append({'path': name, 'bytes': path.stat().st_size, 'sha256': sha256(path.read_bytes()).hexdigest()})
        continue
    keep('native/' + name, path.read_bytes())
source = root / 'data/llm-source/mcp-quality-29a'
seal = json.loads((source / 'manifest.json').read_bytes())
keep('source/manifest.json', (source / 'manifest.json').read_bytes())
for name, pin in seal['files'].items():
    path = (source / 'tree' / name).resolve()
    assert path.is_relative_to(source / 'tree') and sha256(path.read_bytes()).hexdigest() == pin
    keep('source/tree/' + name, path.read_bytes())
k = ['/usr/local/bin/k3s', 'kubectl']; helm = str(root / 'data/runtime/kubernetes-assets-27/helm/helm')
for role in ['retrieval', 'drift']:
    result = subprocess.run([helm, 'history', 'iot-' + role + '-mcp', '-n', 'iot-agents', '-o', 'json'],
        capture_output=True, env={**__import__('os').environ, 'KUBECONFIG': '/etc/rancher/k3s/k3s.yaml'})
    assert result.returncode == 0
    keep('readback/' + role + '-history.json', result.stdout)
keep('curation.json', (json.dumps({'excluded_nonessential': exclusions,
    'reason': 'Coverage database remains on the VM; original coverage JSON and test/container receipts are retained.',
    'credential_scan_passed': True}, indent=2) + '\n').encode())
manifest = {'files': {p.relative_to(target).as_posix(): {'bytes': p.stat().st_size, 'sha256': sha256(p.read_bytes()).hexdigest()}
    for p in sorted(target.rglob('*')) if p.is_file()}}
raw = (json.dumps(manifest, indent=2) + '\n').encode(); (target / 'manifest.json').write_bytes(raw)
archive = root / 'outputs/mcp-quality-capture-29a.tar'; assert not archive.exists()
with tarfile.open(archive, 'w') as package:
    for path in sorted(target.rglob('*')):
        if path.is_file(): package.add(path, arcname='mcp-quality-native/' + path.relative_to(target).as_posix())
report = {'files': len(manifest['files']), 'manifest_sha256': sha256(raw).hexdigest(),
    'archive_bytes': archive.stat().st_size, 'archive_sha256': sha256(archive.read_bytes()).hexdigest()}
(root / 'outputs/mcp-quality-capture-29a.archive.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
