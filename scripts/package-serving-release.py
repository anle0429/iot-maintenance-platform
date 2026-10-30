"""Export only committed serving application/CI/chart/test inputs; never include runtime bundles or credentials."""
from git_sources import resolve_revision
import argparse,io,json,subprocess,tarfile
from hashlib import sha256
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def package(commit,output):
    """Make a deterministic Git-only packet, preserving existing archives and working files."""
    git=lambda *args:subprocess.check_output(['git',*args],cwd=ROOT)
    resolved=resolve_revision(ROOT, commit)
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
    if 'ml/ab_ci.py' in available:
        names+=['ml/ab_ci.py','ml/ab_native.py','tests/test_ab_ci.py','tests/integration/test_ab_ci_http.py']
    if 'ml/serving_gate_reuse.py' in available:
        names+=['ml/serving_gate_reuse.py','tests/test_serving_gate_reuse.py']
    if 'scripts/ci-observability.py' in available:
        names+=['services/observed.py','ml/observability_ci.py','scripts/ci-observability.py','scripts/test-api-observability.py',
            'tests/test_api_observability_gate.py','tests/test_observability_ci.py','tests/api/test_observed.py','tests/api/test_drift_api.py',
            'services/drift_api/app.py','services/drift_api/runtime.py','infra/ci/Observability.Jenkinsfile']
        names+=['infra/observability/'+n for n in ['API.Dockerfile','API.Dockerfile.dockerignore','Tests.Dockerfile','Tests.Dockerfile.dockerignore','requirements.lock','mutmut.toml']]
    if 'scripts/ci-drift-eventing.py' in available:
        names+=['ml/drift_eventing.py','services/drift_api/events.py','tests/api/test_drift_events.py','tests/test_drift_eventing.py',
            'scripts/ci-drift-eventing.py','scripts/test-drift-eventing.py','tests/test_drift_eventing_gate.py','infra/ci/DriftEventing.Jenkinsfile',
            'infra/eventing/API.Dockerfile','infra/eventing/API.Dockerfile.dockerignore','infra/eventing/mutmut.toml',
            'docs/evidence/offline-drift-linux/reference.json','docs/evidence/ci-serving-linux/selected-model6/model-pin.json',
            'docs/evidence/ci-serving-linux/selected-model6/portable.json']
    if 'scripts/ci-stream-push.py' in available:
        names+=['feature_store/'+n+'.py' for n in ['outbox','push','staged_push','staging_contract','staged_checks','staged_job']]
        names+=['pipelines/'+n+'.py' for n in ['stream_job','stream_contract','stream_verification']]
        names+=['tests/fixtures/stream-finalized-native.json','docs/evidence/stream-push/warm/feast/source-report.json',
            'tests/test_stream_staging_contract.py','tests/test_stream_runtime_contract.py','scripts/ci-stream-push.py','infra/ci/StreamPush.Jenkinsfile','infra/ci/iot_ci_stream_push.py',
            'infra/ci/StreamPush.Dockerfile','infra/ci/StreamPush.Dockerfile.dockerignore']
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
