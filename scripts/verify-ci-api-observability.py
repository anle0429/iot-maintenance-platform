"""Verify native telemetry Jenkins2, its complete fresh gate, unchanged model and actual deployed image identities offline."""
from git_sources import historical_blob
from collections import Counter
from hashlib import sha256
from pathlib import Path
import importlib.util,json,re,subprocess,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'docs/evidence/ci-api-observability'


def read(name):
    """Read one retained JSON payload after original-byte validation."""
    return json.loads((BASE/name).read_bytes())


def main():
    """Bind all original native artifacts to source, complete test inventories, model bytes and Pod digests."""
    manifest=read('manifest.json')
    assert set(manifest['files'])=={p.relative_to(BASE).as_posix() for p in BASE.rglob('*') if p.is_file() and p!=BASE/'manifest.json'}
    for name,item in manifest['files'].items():
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        raw=(BASE/name).read_bytes();assert sha256(raw).hexdigest()==item['sha256'] and len(raw)==item['bytes'],name
    original=read('native-manifest.json');assert len(original['files'])==93
    assert all(manifest['files'][n]==v for n,v in original['files'].items())
    source=read('source/current.json');assert source['commit']=='372163145c9e5d56fa02c868cf85a587981ce70c'
    assert len(source['files'])==92 and source['files']==read('source/after.json')['files']
    for name,wanted in source['files'].items():
        raw=historical_blob(ROOT, source['commit']+':'+name)
        assert sha256(raw).hexdigest()==wanted,name
    build=read('jenkins/build-2.json');assert build['number']==2 and not build['building'] and build['result']=='SUCCESS' and build['duration']==274304
    parameters={p['name']:p['value'] for a in build['actions'] for p in a.get('parameters',[])}
    assert parameters=={'SOURCE_COMMIT':source['commit'],'MODEL_VERSION':'6'}
    stages=read('jenkins/stages-2.json')['stages'];assert len(stages)==8 and all(s['status']=='SUCCESS' for s in stages)
    assert 'Jenkinsfile successfully validated.' in (BASE/'control/jenkinsfile-validation.txt').read_text()
    assert read('control/prepared.json')['pipeline_sha256']==source['files']['infra/ci/Observability.Jenkinsfile']
    assert read('jenkins/build-1.json')['result']=='FAILURE'
    assert 'docker.io/library/sha256:' in (BASE/'history/build-1/tests-overlay-build.txt').read_text()
    assert 'pull access denied' in (BASE/'history/build-1/tests-overlay-build.txt').read_text()
    assert 'Ran 8 tests' in (BASE/'current/contracts.txt').read_text() and 'OK' in (BASE/'current/contracts.txt').read_text()
    gate='current/telemetry-tests/gate/'
    junit=ET.parse(BASE/(gate+'junit.xml'));cases=junit.findall('.//testcase');assert len(cases)==299 and junit.find('.//testsuite').get('tests')=='320'
    assert all(not any(c.find(k) is not None for k in ['failure','error','skipped']) for c in cases)
    assert re.search(r'299 passed,.*21 subtests passed',(BASE/(gate+'api-tests.txt')).read_text())
    coverage=read(gate+'coverage.json');c=coverage['totals']
    assert set(coverage['files'])=={'services/observed.py'} and c['num_statements']==c['covered_lines']==74 and c['num_branches']==c['covered_branches']==16 and c['excluded_lines']==0
    meta=read(gate+'observed.py.meta')['exit_code_by_key'];assert Counter(meta.values())=={1:221,0:29}
    rows=re.findall(r'^\s*(services\.observed\.\S+): (killed|survived)\s*$',(BASE/(gate+'mutmut-all-results.txt')).read_text(encoding='utf-8'),re.M)
    assert len(meta)==len(rows)==len(dict(rows))==250 and dict(rows)=={k:'killed' if v==1 else 'survived' for k,v in meta.items()}
    report=read(gate+'verification.json');assert report['commit']==source['commit'] and report['mutation_score_percent']==221/250*100>80
    assert report['regression_main_testcase_elements']==299 and report['regression_suite_reported_tests']==320
    stats=read(gate+'mutmut-cicd-stats.json');assert stats['total']==250 and stats['killed']==221 and stats['survived']==29
    assert all(stats[k]==0 for k in ['no_tests','skipped','suspicious','timeout','check_was_interrupted_by_user','segfault'])
    pin=read('bundle/model-pin.json');portable=read('bundle/portable.json')
    old=ROOT/'docs/evidence/ci-serving-linux/selected-model6'
    assert pin==json.loads((old/'model-pin.json').read_bytes()) and portable==json.loads((old/'portable.json').read_bytes())
    selection=read('current/selection.json')
    for name,wanted in selection['bundle_sha256'].items():assert sha256((BASE/'bundle'/name).read_bytes()).hexdigest()==wanted
    images=read('current/build.json')['images'];assert set(images)=={'feature','drift','predictor'}
    for kind,image in images.items():
        assert image['tag'].endswith(':observe-jenkins-2') and image['packaged_sha256']['services/observed.py']==source['files']['services/observed.py']
        for name,wanted in image['packaged_sha256'].items():
            if name in source['files']:assert wanted==source['files'][name]
        pods=[p for p in read('current/'+kind+'-pods.json')['items'] if not p['metadata'].get('deletionTimestamp')];assert pods
        for pod in pods:
            container=next(c for c in pod['status']['containerStatuses'] if c['name']==('kserve-container' if kind=='predictor' else 'api'))
            assert container['ready'] and container['imageID'].split('://')[-1].split('@')[-1]==image['config_digest']
    assert images['predictor']['packaged_sha256']['portable.json']==pin['portable_sha256']
    spec=importlib.util.spec_from_file_location('ci_model_oracle',ROOT/'scripts/verify-native-serving.py');oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
    oracle.all_machine_rows(read('current/http-all-machines.json'),portable,pin)
    for name in ['select','telemetry-tests','deploy','verify']:
        receipt=read('current/'+name+'-container.json');assert receipt['state']['ExitCode']==0 and not receipt['state']['Running'] and not receipt['state']['OOMKilled']
    test=read('current/telemetry-tests-container.json');assert test['memory_bytes']==4*1024**3 and test['nano_cpus']==2_000_000_000
    assert read('current/verification.json')['model_pin']==pin
    assert all((BASE/'screenshots'/n).read_bytes().startswith(b'\xff\xd8\xff') for n in ['jenkins-telemetry2-success.jpg','jenkins-telemetry2-stages.jpg'])
    print('PASS96payload/92Gitinputs/Jenkins2SUCCESS8stages/8contracts/299main+21subtests/coverage74+16=100%/221of250mutants/3actualimages/200HTTPmodel6/2originalJPEG. First build failure retained; telemetry-only CI, no new model/A-B/Locust claim.')


if __name__=='__main__':main()
