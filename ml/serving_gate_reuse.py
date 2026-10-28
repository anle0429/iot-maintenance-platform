"""Reuse only a complete native mutation result when every API, test, dependency and model input is unchanged."""
from hashlib import sha256
import json
from pathlib import Path
import re
from ml.serving_ci import MUTATION_FILES, test_gate
from services.predictor.binding import validate_pin

CI_ONLY_CHANGES = {'ml/ab_native.py', 'tests/test_ab_ci.py', 'scripts/ci-serving.py',
                   'scripts/test-serving-ci.py', 'scripts/package-serving-release.py', 'infra/ci/Serving.Jenkinsfile'}
NEW_REUSE_FILES = {'ml/serving_gate_reuse.py', 'tests/test_serving_gate_reuse.py'}
REQUIRED = MUTATION_FILES | {'pyproject.toml', 'infra/ci/ServingTests.Dockerfile',
    'infra/ci/requirements-serving-tests.lock', 'ml/serving_ci.py', 'ml/protocol.py',
    'tests/api/test_predictor.py', 'tests/api/test_feature_api.py', 'tests/api/test_model_binding.py', 'tests/api/test_ab_api.py'}


def unchanged_scope(parent, current):
    """Allow only control-plane repairs; reject any API/test/fixture/config/dependency delta or unexpected new file."""
    for source in [parent,current]:
        if source.get('format') != 'iot-sealed-jenkins-serving-v1' or not re.fullmatch('[a-f0-9]{40}',source.get('commit','')):
            raise ValueError('Require sealed serving source manifests')
        if not REQUIRED <= source['files'].keys():
            raise ValueError('Incomplete API/dependency/test source inventory')
    old,new=parent['files'],current['files']
    if set(new) != set(old) | NEW_REUSE_FILES:
        raise ValueError('Unexpected source addition/removal prevents mutation reuse')
    protected=set(old)-CI_ONLY_CHANGES
    if any(old[name] != new[name] for name in protected):
        raise ValueError('API, test, fixture, dependency or model source changed; run fresh mutation')
    return {'protected_files':{name:new[name] for name in sorted(protected)},
            'changed_control_files':sorted(name for name in set(old)&CI_ONLY_CHANGES if old[name]!=new[name]),
            'parent_commit':parent['commit'],'current_commit':current['commit']}


def completed_mutation(parent_source, current_source, parent_outputs, current_requirements, expected_pin):
    """Verify original native hashes, strict full inventory and identical installed dependencies before reuse."""
    load=lambda path:json.loads(Path(path).read_bytes())
    parent,current=load(parent_source),load(current_source)
    scope=unchanged_scope(parent,current)
    parent_outputs=Path(parent_outputs);tests=parent_outputs/'tests'
    selection=load(parent_outputs/'selection.json')
    if (selection['commit']!=parent['commit'] or validate_pin(selection['pin'])!=validate_pin(expected_pin)
            or expected_pin['production'] is not True):
        raise ValueError('Parent source or selected production model differs')
    original=load(tests/'verification.json')
    if original.get('passed') is not True or original.get('mutation_provenance'):
        raise ValueError('Only an original completed native mutation can be reused, not a chain of reused receipts')
    if (tests/'requirements-installed.lock').read_bytes()!=Path(current_requirements).read_bytes():
        raise ValueError('Installed test dependencies differ; run fresh mutation')
    for name in ['coverage.json','mutmut-cicd-stats.json','mutmut-all-results.txt','junit.xml']:
        if sha256((tests/name).read_bytes()).hexdigest()!=original['result_sha256'][name]:
            raise ValueError('Original native API gate artifact changed')
    coverage,stats=load(tests/'coverage.json'),load(tests/'mutmut-cicd-stats.json')
    proof=test_gate(coverage,stats,(tests/'mutmut-all-results.txt').read_text())
    if original['mutation']!=proof['mutation'] or original['mutation_score_percent']!=proof['mutation_score_percent']:
        raise ValueError('Original native score does not match complete mutation inventory')
    return {**scope,'parent_release':selection['release'],
        'original_verification_sha256':sha256((tests/'verification.json').read_bytes()).hexdigest(),
        'requirements_sha256':sha256(Path(current_requirements).read_bytes()).hexdigest(),
        'mutation':proof['mutation'],'mutation_score_percent':proof['mutation_score_percent'],
        'scope':'Completed native mutation reused for byte-identical API/tests/dependencies/model inputs; current contracts, API tests and coverage execute again. Not a resumed/partial mutation run.'}
