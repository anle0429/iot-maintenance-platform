"""Export a trusted committed materialization source packet without working files, secrets or runtime data."""
import argparse
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[1]


def package(commit,output):
    """Use exact Git blobs and a fixed source allowlist; refuse overwriting an earlier packet."""
    resolved=subprocess.check_output(['git','rev-parse','--verify',commit+'^{commit}'],cwd=ROOT,text=True).strip()
    files=subprocess.check_output(['git','ls-tree','-r','--name-only',resolved,'feature_store'],cwd=ROOT,text=True).splitlines()
    files+=['ml/__init__.py','ml/protocol.py','ml/protocols/medium-60d-v1.json',
        'infra/ci/Materialization.Dockerfile','infra/ci/Materialization.Dockerfile.dockerignore',
        'infra/ci/Materialization.Jenkinsfile','infra/ci/iot_ci_materialization.py',
        'scripts/ci-materialization.py','tests/test_ci_materialization.py']
    manifest_path=output.with_suffix('.manifest.json')
    if output.exists() or manifest_path.exists():
        raise FileExistsError('Use a new packet name; existing release evidence is immutable')
    output.parent.mkdir(parents=True,exist_ok=True)
    manifest={'format':'iot-sealed-jenkins-materialization-v1','commit':resolved,'files':{}}
    with tarfile.open(output,'x') as archive:
        for name in sorted(set(files)):
            raw=subprocess.check_output(['git','show',resolved+':'+name],cwd=ROOT)
            manifest['files'][name]=sha256(raw).hexdigest()
            member=tarfile.TarInfo('tree/'+name);member.size=len(raw);member.mode=0o644
            archive.addfile(member,io.BytesIO(raw))
    manifest['archive_sha256']=sha256(output.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'commit':resolved,'files':len(manifest['files']),'archive_sha256':manifest['archive_sha256']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit',required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();package(args.commit,args.output)
