"""Reject changed or incomplete native mutation artifacts while permitting a strictly scoped adapter-only repair."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
import pytest
from ml.serving_gate_reuse import REQUIRED, NEW_REUSE_FILES, unchanged_scope, completed_mutation
from services.predictor.binding import LEGACY
from ml.serving_ci import MUTATION_FILES


def sources():
    """Declare fixture source seals; these hashes do not represent a native execution."""
    names=REQUIRED|{'ml/ab_native.py','tests/test_ab_ci.py','models/pump-failure-v1/portable.json'}
    parent={'format':'iot-sealed-jenkins-serving-v1','commit':'a'*40,'files':{n:'1'*64 for n in names}}
    current=deepcopy(parent);current['commit']='b'*40
    current['files'].update({n:'2'*64 for n in NEW_REUSE_FILES});current['files']['ml/ab_native.py']='3'*64
    return parent,current


def test_only_the_explicit_control_repair_is_exempt_from_api_identity():
    parent,current=sources();scope=unchanged_scope(parent,current)
    assert scope['changed_control_files']==['ml/ab_native.py']
    assert REQUIRED<=scope['protected_files'].keys()
    for name in REQUIRED|{'models/pump-failure-v1/portable.json'}:
        bad=deepcopy(current);bad['files'][name]='f'*64
        with pytest.raises(ValueError,match='changed'):unchanged_scope(parent,bad)


@pytest.mark.parametrize('edit',['missing','extra','format','commit'])
def test_partial_or_unexpected_source_inventories_fail(edit):
    parent,current=sources()
    if edit=='missing':del current['files']['services/predictor/runtime.py']
    elif edit=='extra':current['files']['services/predictor/unreviewed.py']='f'*64
    elif edit=='format':parent['format']='unsealed'
    else:current['commit']='branch-name'
    with pytest.raises(ValueError):unchanged_scope(parent,current)


def artifact_fixture(tmp_path):
    """Write seven explicitly synthetic killed statuses to exercise verification, never claim a real mutation score."""
    parent,current=sources();folder=tmp_path/'parent';tests=folder/'tests';tests.mkdir(parents=True)
    dump=lambda path,value:path.write_text(json.dumps(value)+'\n')
    dump(tmp_path/'parent.json',parent);dump(tmp_path/'current.json',current)
    pin={**LEGACY,'version':'6','production':True}
    dump(folder/'selection.json',{'commit':parent['commit'],'pin':pin,'release':'serve-jenkins-5'})
    coverage={'totals':{'num_statements':2,'covered_lines':2,'num_branches':2,'covered_branches':2,'excluded_lines':0}}
    statuses='\n'.join(name[:-3].replace('/','.')+'.x_known_fixture__mutmut_1: killed' for name in sorted(MUTATION_FILES))+'\n'
    stats={k:0 for k in ['survived','no_tests','skipped','suspicious','timeout','check_was_interrupted_by_user','segfault']};stats.update(total=7,killed=7)
    dump(tests/'coverage.json',coverage);dump(tests/'mutmut-cicd-stats.json',stats);(tests/'mutmut-all-results.txt').write_text(statuses)
    (tests/'junit.xml').write_text('<testsuite tests="1"/>');(tests/'requirements-installed.lock').write_text('declared-fixture==1\n')
    current_freeze=tmp_path/'freeze.lock';current_freeze.write_bytes((tests/'requirements-installed.lock').read_bytes())
    proof={'passed':True,'mutation':stats,'mutation_score_percent':100.,'result_sha256':{n:sha256((tests/n).read_bytes()).hexdigest() for n in ['coverage.json','mutmut-cicd-stats.json','mutmut-all-results.txt','junit.xml']}}
    dump(tests/'verification.json',proof)
    return tmp_path/'parent.json',tmp_path/'current.json',folder,current_freeze,pin


def test_complete_artifacts_are_checked_against_original_hashes_and_installed_dependencies(tmp_path):
    args=artifact_fixture(tmp_path);receipt=completed_mutation(*args)
    assert receipt['parent_release']=='serve-jenkins-5' and receipt['mutation']['total']==7
    (args[2]/'tests/mutmut-all-results.txt').write_text('incomplete')
    with pytest.raises(ValueError,match='changed'):completed_mutation(*args)


@pytest.mark.parametrize('fault',['failed','chain','deps','pin','partial'])
def test_failed_chained_mismatched_or_partial_gate_never_gets_reuse_credit(tmp_path,fault):
    args=list(artifact_fixture(tmp_path));path=args[2]/'tests/verification.json';proof=json.loads(path.read_bytes())
    if fault=='failed':proof['passed']=False
    elif fault=='chain':proof['mutation_provenance']={'parent':'older'}
    elif fault=='deps':args[3].write_text('different==2\n')
    elif fault=='pin':args[4]={**args[4],'version':'7'}
    else:
        target=args[2]/'tests/mutmut-cicd-stats.json';stats=json.loads(target.read_bytes());stats['killed']=6;stats['timeout']=1
        target.write_text(json.dumps(stats)+'\n');proof['result_sha256'][target.name]=sha256(target.read_bytes()).hexdigest()
    path.write_text(json.dumps(proof)+'\n')
    with pytest.raises(ValueError):completed_mutation(*args)


def test_driver_mounts_only_an_earlier_gate_readonly_in_a_fresh_bounded_test_container(tmp_path,monkeypatch):
    """Exercise the real new driver branch with explicit command fixtures, including its parent-ID validation."""
    root=Path(__file__).resolve().parents[1];driver=runpy.run_path(str(root/'scripts/ci-serving.py'))
    parent=tmp_path/'data/ci/releases/serve-jenkins-5/outputs';parent.mkdir(parents=True)
    (parent/'selection.json').write_text(json.dumps({'commit':'a'*40}))
    out=tmp_path/'current/outputs';out.mkdir(parents=True);commands=[]
    context=SimpleNamespace(release='serve-jenkins-6',out=out,deploy=tmp_path,commit='b'*40,
        directory=tmp_path/'data/ci/releases/serve-jenkins-6',selected=lambda:None,
        log=lambda *args:commands.append(args),container=lambda *args,**kwargs:commands.append((args,kwargs)),source_check=lambda:None)
    monkeypatch.setitem(driver['run'].__globals__,'Context',lambda:context)
    monkeypatch.setitem(driver['run'].__globals__,'real_admission',lambda *args:{'fixture_only':True})
    monkeypatch.setenv('BUILD_NUMBER','6');monkeypatch.setenv('IOT_API_GATE_PARENT','5')
    driver['run']('test')
    args,limits=commands[-1];command=args[1]
    assert limits=={'memory':'4g','cpus':'2'} and command[command.index('--network')+1]=='none'
    assert '--parent-outputs' in command and '--current-source' in command and '--expected-pin' in command
    assert any(str(parent) in arg and 'readonly' in arg for arg in command)
    assert '--pids-limit' in command and '--cap-drop' in command and '--privileged' not in command


@pytest.mark.parametrize('parent',['0','6','7','../5','5;touch x'])
def test_driver_rejects_a_current_future_or_noncanonical_parent(tmp_path,monkeypatch,parent):
    root=Path(__file__).resolve().parents[1];driver=runpy.run_path(str(root/'scripts/ci-serving.py'));out=tmp_path/'out';out.mkdir()
    context=SimpleNamespace(release='serve-jenkins-6',out=out,deploy=tmp_path,commit='b'*40,
        directory=tmp_path/'release',selected=lambda:None,log=lambda *args:None)
    monkeypatch.setitem(driver['run'].__globals__,'Context',lambda:context)
    monkeypatch.setitem(driver['run'].__globals__,'real_admission',lambda *args:{'fixture_only':True})
    monkeypatch.setenv('BUILD_NUMBER','6');monkeypatch.setenv('IOT_API_GATE_PARENT',parent)
    with pytest.raises(ValueError,match='earlier canonical'):driver['run']('test')
