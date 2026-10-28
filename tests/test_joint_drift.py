"""Check joint drift against analytic kernels, exact marginals, replay and invalid availability boundaries."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json,math
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest
from hypothesis import given,settings,strategies as st
from ml.joint_drift import JointDriftAudit,gaussian_gram,partition_mmd_squared,load_policy,POLICY_SHA256
from services.drift_api.contracts import CompareRequest
from services.drift_api.detector import canonical_bytes

ROOT=Path(__file__).resolve().parents[1]


def cohorts(size=64,reverse=False):
    """Create the declared temperature/vibration association switch with exactly equal column multisets."""
    values=[-1+2*i/(size-1) for i in range(size)]
    rows=[{'temperature_mean_10m':50+20*x,'vibration_max_10m':3+2*x,'current_mean_10m':12.0,
        'current_std_10m':1.0,'rpm_mean_10m':3000.0,'reading_count_10m':10,'machine_age_days':180.0,'prior_failures_24h':0} for x in values]
    current=deepcopy(rows)
    if reverse:
        for i,row in enumerate(current):row['vibration_max_10m']=rows[-1-i]['vibration_max_10m']
    bundle={'format':'iot-drift-reference-v1','lineage':{'fixture':'declared-association-switch'},
        'policy':{'bins':10,'psi_threshold':.2,'drifted_feature_fraction':.25,'pseudocount':.5},'samples':rows}
    window={'reference_id':sha256(canonical_bytes(bundle)).hexdigest(),'window_id':'known-window',
        'start':'2026-10-01T00:00:00Z','end':'2026-10-01T01:00:00Z',
        'observations':[{'id':f'fixture-{i:03d}','event_timestamp':'2026-10-01T00:30:00Z',
            'available_at':'2026-10-01T00:31:00Z','features':row} for i,row in enumerate(current)]}
    return bundle,window


def test_scalar_kernel_and_statistic_match_analytic_two_point_answer():
    'Verify scalar kernel and statistic match analytic two point answer.'
    gram=gaussian_gram([(0.0,),(1.0,)],[1.0],1.0);k=math.exp(-.5)
    assert list(gram)==pytest.approx([1,k,k,1])
    assert partition_mmd_squared(gram,[1+k,1+k],2+2*k,[0])==pytest.approx(2-2*k)


def test_unequal_groups_match_independent_direct_summation():
    'Verify unequal groups match independent direct summation.'
    points=[(0.0,),(1.0,),(3.0,),(4.0,),(5.0,)];left=[0,3];right=[1,2,4]
    kernel=lambda a,b:math.exp(-((points[a][0]-points[b][0])**2)/2)
    expected=sum(kernel(i,j) for i in left for j in left)/4+sum(kernel(i,j) for i in right for j in right)/9-2*sum(kernel(i,j) for i in left for j in right)/6
    gram=gaussian_gram(points,[1],1);sums=[sum(gram[i*5:(i+1)*5]) for i in range(5)]
    assert partition_mmd_squared(gram,sums,sum(sums),left)==pytest.approx(expected)


def test_joint_shift_alerts_while_all_eight_marginal_psis_are_exactly_zero():
    'Verify joint shift alerts while all eight marginal psis are exactly zero.'
    bundle,window=cohorts(reverse=True)
    for name in bundle['samples'][0]:assert sorted(row[name] for row in bundle['samples'])==sorted(row['features'][name] for row in window['observations'])
    result=JointDriftAudit(bundle).compare(CompareRequest.model_validate(window))
    assert all(v['psi']==0 for v in result['psi']['features'].values()) and not result['psi']['drift_detected']
    assert result['joint']['mmd_squared']>0 and result['joint']['diagnostic_alert']
    assert result['joint']['permutation_tail_fraction']<=.05
    assert result['joint']['p_value'] is None and result['joint']['exchangeability_verified'] is False


def test_identical_group_reordering_and_exact_id_replay_keep_the_assessment():
    'Verify identical group reordering and exact id replay keep the assessment.'
    bundle,window=cohorts();audit=JointDriftAudit(bundle);normal=audit.compare(CompareRequest.model_validate(window))
    assert normal['joint']['mmd_squared']==pytest.approx(0,abs=1e-12)
    assert normal['joint']['permutation_tail_fraction']==1 and not normal['joint']['diagnostic_alert']
    retry=deepcopy(window);retry['observations'].reverse();retry['observations']+=deepcopy(retry['observations'][:8])
    assert audit.compare(CompareRequest.model_validate(retry))==normal


def test_identical_constant_groups_keep_all_permutation_ties():
    'Verify identical constant groups keep all permutation ties.'
    bundle,window=cohorts(32)
    for row in bundle['samples']:row.update(deepcopy(bundle['samples'][0]))
    for row in window['observations']:row['features']=deepcopy(bundle['samples'][0])
    window['reference_id']=sha256(canonical_bytes(bundle)).hexdigest()
    joint=JointDriftAudit(bundle).compare(CompareRequest.model_validate(window))['joint']
    assert joint['mmd_squared']==0 and joint['permutation_tail_fraction']==1 and joint['permutation_exceedances']==199
    assert joint['permutation_statistics']==[0]*199


def test_clear_marginal_sensor_offset_is_seen_by_both_detectors():
    'Verify clear marginal sensor offset is seen by both detectors.'
    bundle,window=cohorts()
    for row in window['observations']:row['features']['temperature_mean_10m']+=80;row['features']['vibration_max_10m']+=8
    result=JointDriftAudit(bundle).compare(CompareRequest.model_validate(window))
    assert result['psi']['drift_detected'] and result['joint']['diagnostic_alert']


def test_nonlinear_relation_shift_can_keep_both_marginals_and_zero_covariance():
    """Use three temperature levels; move low vibration from the center to balanced tails."""
    bundle,window=cohorts(96)
    for i,row in enumerate(bundle['samples']):
        level=i//32-1;row['temperature_mean_10m']=50+20*level;row['vibration_max_10m']=1.0 if level==0 else 5.0
        current=window['observations'][i]['features'];current['temperature_mean_10m']=row['temperature_mean_10m']
        current['vibration_max_10m']=1.0 if level!=0 and i%32<16 else 5.0
    window['reference_id']=sha256(canonical_bytes(bundle)).hexdigest()
    def covariance(rows):
        'Compute population covariance directly for the nonlinear counterexample oracle.'
        x=[r['temperature_mean_10m'] for r in rows];y=[r['vibration_max_10m'] for r in rows]
        return sum((a-sum(x)/len(x))*(b-sum(y)/len(y)) for a,b in zip(x,y))/len(x)
    current=[r['features'] for r in window['observations']]
    assert covariance(bundle['samples'])==pytest.approx(0,abs=1e-12) and covariance(current)==pytest.approx(0,abs=1e-12)
    for name in current[0]:assert sorted(r[name] for r in current)==sorted(r[name] for r in bundle['samples'])
    result=JointDriftAudit(bundle).compare(CompareRequest.model_validate(window))
    assert not result['psi']['drift_detected'] and result['joint']['diagnostic_alert']


def test_maximum_declared_group_uses_exact_bounded_float64_payload():
    'Verify maximum declared group uses exact bounded float64 payload.'
    bundle,window=cohorts(128);result=JointDriftAudit(bundle).compare(CompareRequest.model_validate(window))
    assert result['resources']=={'matrix_rows':256,'gram_payload_bytes':524288,'gram_payload_cap_bytes':524288}
    assert not result['joint']['diagnostic_alert']


@pytest.mark.parametrize('size',[31,129])
def test_reference_bounds_reject_before_allocating_gram(size):
    'Verify reference bounds reject before allocating gram.'
    bundle,_=cohorts(size)
    with patch('ml.joint_drift.gaussian_gram',side_effect=AssertionError('Must not allocate')):
        with pytest.raises(ValueError,match='32..128'):JointDriftAudit(bundle)


def test_oversized_window_and_wrong_reference_never_allocate_gram():
    'Verify oversized window and wrong reference never allocate gram.'
    bundle,window=cohorts(129);bundle['samples']=bundle['samples'][:128];audit=JointDriftAudit(bundle)
    window['reference_id']=audit.detector.reference_id
    with patch('ml.joint_drift.gaussian_gram',side_effect=AssertionError('Must not allocate')):
        with pytest.raises(ValueError,match='exceeds128'):audit.compare(CompareRequest.model_validate(window))
        window['reference_id']='f'*64
        with pytest.raises(ValueError,match='Reference does not match'):audit.compare(CompareRequest.model_validate(window))


@pytest.mark.parametrize('change',[
    lambda w:w['observations'][0].update(available_at='2026-10-01T01:00:01Z'),
    lambda w:w['observations'][0].update(event_timestamp='2026-10-01T01:00:00Z'),
    lambda w:w['observations'][0]['features'].update(label=1),
    lambda w:w['observations'][0]['features'].update(current_mean_10m=float('nan')),
    lambda w:w['observations'].append({**w['observations'][0],'available_at':'2026-10-01T00:32:00Z'})])
def test_existing_window_contract_rejects_invalid_time_label_and_conflicting_replay(change):
    'Verify existing window contract rejects invalid time label and conflicting replay.'
    _,window=cohorts();change(window)
    with pytest.raises(ValueError):CompareRequest.model_validate(window)


def test_policy_and_reference_cannot_change_by_input_or_output_alias(tmp_path):
    'Verify policy and reference cannot change by input or output alias.'
    path=tmp_path/'policy.json';path.write_bytes(b'{}')
    with pytest.raises(ValueError,match='frozen'):load_policy(path)
    bundle,window=cohorts(32);audit=JointDriftAudit(bundle);bundle['samples'][0]['current_mean_10m']=99
    result=audit.compare(CompareRequest.model_validate(window));result['policy']['sigma']=100;result['psi']['policy']['psi_threshold']=100
    assert audit.policy['sigma']==1 and audit.detector.policy['psi_threshold']==.2
    assert audit.compare(CompareRequest.model_validate(window))['joint']['mmd_squared']==pytest.approx(0,abs=1e-12)


@settings(max_examples=10,deadline=None)
@given(st.lists(st.floats(min_value=-100,max_value=100,allow_nan=False,allow_infinity=False),min_size=2,max_size=8))
def test_gaussian_symmetry_and_equal_multiset_statistic(values):
    'Verify gaussian symmetry and equal multiset statistic.'
    points=[(x,) for x in values];gram=gaussian_gram(points,[20.0],1.0);size=len(points)
    assert all(gram[i*size+i]==1 and 0<=gram[i*size+j]<=1 and gram[i*size+j]==gram[j*size+i] for i in range(size) for j in range(size))
    doubled=points+points;gram=gaussian_gram(doubled,[20.0],1);count=len(doubled)
    sums=[math.fsum(gram[i*count:(i+1)*count]) for i in range(count)]
    assert partition_mmd_squared(gram,sums,math.fsum(sums),list(range(size)))==pytest.approx(0,abs=1e-12)


def test_public_cli_preserves_inputs_and_existing_output(tmp_path,capsys):
    'Verify public cli preserves inputs and existing output.'
    spec=importlib.util.spec_from_file_location('joint_cli_test',ROOT/'scripts/audit-joint-drift.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    bundle,window=cohorts(32,reverse=True);ref=tmp_path/'reference.json';win=tmp_path/'window.json';out=tmp_path/'result.json'
    ref.write_bytes(canonical_bytes(bundle));win.write_bytes(canonical_bytes(window));before=[ref.read_bytes(),win.read_bytes()]
    module.run(NS(reference=ref,window=win,output=out));result=json.loads(out.read_text())
    assert result['joint']['diagnostic_alert'] and result['policy_sha256']==POLICY_SHA256
    assert [ref.read_bytes(),win.read_bytes()]==before
    report=out.read_bytes()
    with pytest.raises(FileExistsError):module.run(NS(reference=ref,window=win,output=out))
    assert out.read_bytes()==report
    oversized=tmp_path/'oversized.json';oversized.write_bytes(b' '*(1024*1024+1))
    with pytest.raises(ValueError,match='exceeds1MiB'):module.read_json(oversized)
