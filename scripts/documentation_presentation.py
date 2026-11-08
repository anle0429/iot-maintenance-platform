"""Authenticate reviewed documentation changes before restoring historical document bindings."""
from pathlib import Path
from hashlib import sha256
import json
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PRESENTATION_SHA256='136d5ddaa173c58c994d761c4fd823ce318cb0e39c027c4e59e64a423e4645c4'


def presentation():
    """Pin the exact old/new page patches and authored-asset inventory."""
    body=(ROOT/'docs/presentation.json').read_bytes().replace(b'\r\n',b'\n')
    if sha256(body).hexdigest()!=PRESENTATION_SHA256:raise ValueError('Figure presentation inventory changed')
    value=json.loads(body)
    assert value['base_commit']=='8575de6535262c1abda7d961a298a8d9705df412'
    assert value['files'] and value['assets']
    return value


def before_presentation(name,current):
    """Accept exact declared presentation patches, then return the untouched predecessor text."""
    value=presentation()
    if name not in value['files']:return current
    row=value['files'][name];current=current.replace(b'\r\n',b'\n')
    original=subprocess.check_output(['git','--no-replace-objects','show',value['base_commit']+':'+name],cwd=ROOT)
    assert sha256(original).hexdigest()==row['before_sha256'],name
    if row.get('reviewed_replacement') is True:
        assert sha256(current).hexdigest()==row['after_sha256'],name
        return original
    lines=original.decode().splitlines(keepends=True)
    for patch in reversed(row['patches']):
        start=patch['line'];old=patch['old'].splitlines(keepends=True)
        assert lines[start:start+len(old)]==old,name
        lines[start:start+len(old)]=patch['new'].splitlines(keepends=True)
    expected=''.join(lines).encode()
    assert sha256(expected).hexdigest()==row['after_sha256'],name
    assert current==expected,name
    return original


def check_evidence_additions(base, assets, trees):
    """Permit only exact declared new receipts while rejecting changes to any original file."""
    changed=subprocess.check_output(['git','diff','--no-renames','--name-only','-z',base,'--',*trees],cwd=ROOT)
    original=set(subprocess.check_output(['git','ls-tree','-r','--name-only','-z',base,'--',*trees],cwd=ROOT).decode().split('\0'))
    for name in filter(None,changed.decode().split('\0')):
        assert name not in original and name in assets and assets[name]['new'],name
        assert assets[name]['mode']=='exact_bytes' and sha256((ROOT/name).read_bytes()).hexdigest()==assets[name]['sha256'],name
