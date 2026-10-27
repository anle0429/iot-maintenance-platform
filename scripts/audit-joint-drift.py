"""Run a bounded label-free joint drift audit from explicit reference/window JSON files."""
import argparse,json,sys
from hashlib import sha256
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.joint_drift import JointDriftAudit
from services.drift_api.contracts import CompareRequest


def read_json(path):
    """Reject oversized inputs before decoding or constructing the sensor cohort."""
    with path.open('rb') as source:raw=source.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('Joint drift input exceeds1MiB')
    return json.loads(raw),sha256(raw).hexdigest()


def run(args):
    """Preserve existing reports and record exact input identities without reading future labels."""
    if args.output.exists():raise FileExistsError('Preserve the existing joint drift audit')
    bundle,reference_sha=read_json(args.reference);window,window_sha=read_json(args.window)
    result=JointDriftAudit(bundle).compare(CompareRequest.model_validate(window))
    result['input_sha256']={'reference':reference_sha,'window':window_sha}
    raw=(json.dumps(result,indent=2,allow_nan=False)+'\n').encode()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('xb') as target:target.write(raw)
    print(json.dumps({'report_sha256':sha256(raw).hexdigest(),'reference_id':result['reference_id'],
        'mmd_squared':result['joint']['mmd_squared'],'diagnostic_alert':result['joint']['diagnostic_alert'],
        'psi_alert':result['psi']['drift_detected'],'scope':'Bounded diagnostic only; no calibrated p-value or automatic retraining'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['reference','window','output']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
