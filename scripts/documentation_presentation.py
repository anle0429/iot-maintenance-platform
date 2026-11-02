"""Authenticate reviewed figure-placement edits before restoring historical document bindings."""
from pathlib import Path
from hashlib import sha256
import json
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PRESENTATION_SHA256='1bc3e77917e174772bbaa94245de3dff16bd24206bba564795b06589d11f95d2'


def presentation():
    """Pin the exact old/new page patches and authored-asset inventory."""
    body=(ROOT/'docs/presentation.json').read_bytes().replace(b'\r\n',b'\n')
    if sha256(body).hexdigest()!=PRESENTATION_SHA256:raise ValueError('Figure presentation inventory changed')
    value=json.loads(body)
    assert value['base_commit']=='8575de6535262c1abda7d961a298a8d9705df412'
    assert len(value['files'])==8
    return value


def before_presentation(name,current):
    """Accept exact declared presentation patches, then return the untouched predecessor text."""
    value=presentation()
    if name not in value['files']:return current
    row=value['files'][name];current=current.replace(b'\r\n',b'\n')
    original=subprocess.check_output(['git','--no-replace-objects','show',value['base_commit']+':'+name],cwd=ROOT)
    assert sha256(original).hexdigest()==row['before_sha256'],name
    lines=original.decode().splitlines(keepends=True)
    for patch in reversed(row['patches']):
        start=patch['line'];old=patch['old'].splitlines(keepends=True)
        assert lines[start:start+len(old)]==old,name
        lines[start:start+len(old)]=patch['new'].splitlines(keepends=True)
    expected=''.join(lines).encode()
    assert sha256(expected).hexdigest()==row['after_sha256'],name
    assert current==expected,name
    return original
