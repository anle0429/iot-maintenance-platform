"""Export only committed training application, CI configuration and tests for a trusted private Jenkins release."""
import argparse
import ast
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import tarfile
ROOT=Path(__file__).resolve().parents[1]


def package(commit,output):
    """Keep all runtime imports sealed and preserve previous packets and working changes."""
    git=lambda *args:subprocess.check_output(['git',*args],cwd=ROOT)
    resolved=git('rev-parse','--verify',commit+'^{commit}').decode().strip()
    contract=ast.parse(git('show',resolved+':ml/versioned_contract.py').decode())
    guarded=next(ast.literal_eval(node.value) for node in contract.body if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Name) and target.id=='SOURCE_FILES' for target in node.targets))
    files=git('ls-tree','-r','--name-only',resolved,'ml','feature_store','infra/kfp','infra/ml-versioning').decode().splitlines()
    files+=guarded+['scripts/ci-training.py','scripts/compile-ci-training.py','scripts/package-training-release.py',
        'infra/ci/Training.Jenkinsfile','infra/ci/Training.Dockerfile','infra/ci/Training.Dockerfile.dockerignore',
        'infra/ci/TrainingController.Dockerfile','infra/ci/TrainingController.Dockerfile.dockerignore',
        'infra/ci/requirements-training-controller.lock','tests/test_ci_training.py','tests/test_automatic_versions.py',
        'tests/test_data_versions.py','tests/test_model_data_link.py','tests/test_notebook_holdout_gate.py']
    if 'tests/__init__.py' in git('ls-tree','-r','--name-only',resolved,'tests').decode().splitlines():
        files+=['tests/__init__.py']
    manifest_path=output.with_suffix('.manifest.json')
    if output.exists() or manifest_path.exists():raise FileExistsError('Preserve previous release packets')
    output.parent.mkdir(parents=True,exist_ok=True)
    manifest={'format':'iot-sealed-jenkins-training-v1','commit':resolved,'files':{}}
    with tarfile.open(output,'x') as archive:
        for name in sorted(set(files)):
            raw=git('show',resolved+':'+name);manifest['files'][name]=sha256(raw).hexdigest()
            member=tarfile.TarInfo('tree/'+name);member.size=len(raw);member.mode=0o644
            archive.addfile(member,io.BytesIO(raw))
    manifest['archive_sha256']=sha256(output.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'commit':resolved,'files':len(manifest['files']),'guarded_ml_files':len(guarded),'archive_sha256':manifest['archive_sha256']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();package(args.commit,args.output)
