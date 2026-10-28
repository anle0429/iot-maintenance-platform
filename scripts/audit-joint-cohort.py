"""Bind a predeclared bounded joint-drift cohort to real training-reference and retained gateway captures."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime,timedelta
from copy import deepcopy
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.joint_drift import JointDriftAudit
from services.drift_api.contracts import CompareRequest


def audit(reference_path,current_path,output):
    """Select once by frozen rule, retain all cases including a possibly negative counterfactual, and check replay."""
    output.mkdir(parents=True,exist_ok=False)
    policy_raw=(ROOT/'ml/protocols/joint-cohort-v1.json').read_bytes();policy=json.loads(policy_raw);assert policy['rows_per_group']==64
    reference_raw=reference_path.read_bytes();current_raw=current_path.read_bytes();parent=json.loads(reference_raw);captured=json.loads(current_raw)
    assert parent['lineage']['partition']=='train' and len(parent['samples'])==1024
    rows=sorted([r['features'] for r in captured if r['prediction_status']==200 and r['features']['operating_context']['status']=='operating'],key=lambda r:r['machine_id'])[:64];assert len(rows)==64
    assert len({r['machine_id'] for r in rows})==64 and all(r['protocol_sha256']==parent['lineage']['protocol_sha256'] for r in rows)
    parents={'reference_sha256':sha256(reference_raw).hexdigest(),'current_sha256':sha256(current_raw).hexdigest(),'cohort_policy_sha256':sha256(policy_raw).hexdigest()}
    reference={'format':parent['format'],'policy':parent['policy'],'lineage':{'parent_training_reference':parent['lineage'],**parents,'selection':policy['reference']},'samples':parent['samples'][:64]}
    detector=JointDriftAudit(reference);start=min(datetime.fromisoformat(r['feature_event_ts']) for r in rows);end=max(datetime.fromisoformat(r['as_of']) for r in rows)+timedelta(microseconds=1)
    observations=[{'id':sha256((r['machine_id']+'|'+r['feature_event_ts']).encode()).hexdigest(),'event_timestamp':r['feature_event_ts'],'available_at':r['as_of'],'features':r['features']} for r in rows]
    current={'reference_id':detector.detector.reference_id,'window_id':'retained-gateway-cohort-28','start':start.isoformat(),'end':end.isoformat(),'observations':observations}
    identical=deepcopy(current);identical['window_id']='identical-reference-control';identical['observations']=[{**v,'features':vector} for v,vector in zip(identical['observations'],reference['samples'],strict=True)]
    rotated=deepcopy(current);rotated['window_id']='current-with-vibration-rotated-left-one';values=[v['features']['vibration_max_10m'] for v in current['observations']]
    for index,row in enumerate(rotated['observations']):row['features']['vibration_max_10m']=values[(index+1)%64]
    for name in current['observations'][0]['features']:assert sorted(v['features'][name] for v in current['observations'])==sorted(v['features'][name] for v in rotated['observations'])
    reports={}
    for name,window in [('control',identical),('current',current),('rotated',rotated)]:
        result=detector.compare(CompareRequest.model_validate(window));reports[name]=result
        for suffix,value in [('window',window),('report',result)]:(output/(name+'-'+suffix+'.json')).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    assert reports['control']['joint']['mmd_squared']==0 and not reports['control']['joint']['diagnostic_alert']
    replay=deepcopy(current);replay['observations']=list(reversed(replay['observations']))+[replay['observations'][0]];assert detector.compare(CompareRequest.model_validate(replay))==reports['current']
    (output/'reference.json').write_text(json.dumps(reference,indent=2)+'\n');(output/'replay-window.json').write_text(json.dumps(replay,indent=2)+'\n')
    result={'passed':True,'parent_inputs':parents,'parent_reference_lineage':parent['lineage'],'selected_machine_ids':[r['machine_id'] for r in rows],'rows_per_group':64,'canonical_replay_equal':True,'all_counterfactual_marginals_preserved':True,'reports':{n:{'mmd_squared':v['joint']['mmd_squared'],'tail_fraction':v['joint']['permutation_tail_fraction'],'diagnostic_alert':v['joint']['diagnostic_alert'],'psi_alert':v['psi']['drift_detected']} for n,v in reports.items()},'scope':policy['scope']}
    (output/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--reference',type=Path,required=True);p.add_argument('--current',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();audit(a.reference,a.current,a.output)
