"""Export only committed serving application/CI/chart/test inputs; never include runtime bundles or credentials."""
import argparse,io,json,subprocess,tarfile
from hashlib import sha256
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def package(commit,output):
    """Make a deterministic Git-only packet, preserving existing archives and working files."""
    git=lambda *args:subprocess.check_output(['git',*args],cwd=ROOT)
    resolved=git('rev-parse','--verify',commit+'^{commit}').decode().strip()
    names=git('ls-tree','-r','--name-only',resolved,'services/predictor','services/feature_api','infra/charts/iot-api').decode().splitlines()
    names+=['services/__init__.py','ml/__init__.py','ml/protocol.py','ml/training_contract.py','ml/serving_release.py','ml/serving_ci.py',
        'ml/protocols/medium-60d-v1.json','pyproject.toml','tests/__init__.py','tests/api/test_predictor.py','tests/api/test_feature_api.py',
        'tests/api/test_model_binding.py','tests/test_serving_release.py','tests/test_serving_ci.py',
        'tests/fixtures/predictor-native-spark.json','tests/fixtures/feature-api-native-online.json',
        'scripts/ci-serving.py','scripts/test-serving-ci.py','scripts/package-serving-release.py','scripts/write-model-pin.py','scripts/export-production-model.py',
        'infra/ci/Serving.Jenkinsfile','infra/ci/ServingTests.Dockerfile','infra/ci/ServingTests.Dockerfile.dockerignore',
        'infra/ci/ServingController.Dockerfile','infra/ci/ServingController.Dockerfile.dockerignore','infra/ci/requirements-serving-tests.lock',
        'infra/predictor/Dockerfile','infra/predictor/Dockerfile.dockerignore','infra/predictor/requirements.lock',
        'infra/feature-api/Dockerfile','infra/feature-api/Dockerfile.dockerignore','infra/feature-api/helm-values.yaml','infra/feature-api/redis.yaml','infra/kserve/pump-failure.yaml']
    names+=['feature_store/'+n+'.py' for n in ['__init__','common','repository','batch','serving']]
    names+=['models/pump-failure-v1/'+n+'.json' for n in ['portable','lineage','model-pin']]
    available=set(git('ls-tree','-r','--name-only',resolved).decode().splitlines())
    if 'services/ab_api/runtime.py' in available:
        names+=['services/ab_api/'+name+'.py' for name in ['__init__','app','runtime','metrics']]
        names+=['services/drift_api/'+name+'.py' for name in ['__init__','contracts','detector']]
        names+=['tests/api/test_ab_api.py','tests/integration/test_ab_routing_http.py',
            'infra/ab-api/Dockerfile','infra/ab-api/Dockerfile.dockerignore','infra/ab-api/helm-values.yaml','infra/ab-api/grafana-dashboard.json']
    if set(names)-available:raise ValueError('Serving source commit lacks required files: '+','.join(sorted(set(names)-available)))
    manifest_path=output.with_suffix('.manifest.json')
    if output.exists() or manifest_path.exists():raise FileExistsError('Preserve previous serving source packets')
    output.parent.mkdir(parents=True,exist_ok=True)
    manifest={'format':'iot-sealed-jenkins-serving-v1','commit':resolved,'files':{}}
    with tarfile.open(output,'x') as archive:
        for name in sorted(set(names)):
            raw=git('show',resolved+':'+name);manifest['files'][name]=sha256(raw).hexdigest()
            member=tarfile.TarInfo('tree/'+name);member.size=len(raw);member.mode=0o644;archive.addfile(member,io.BytesIO(raw))
    manifest['archive_sha256']=sha256(output.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'commit':resolved,'files':len(manifest['files']),'archive_sha256':manifest['archive_sha256']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--commit',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();package(args.commit,args.output)
