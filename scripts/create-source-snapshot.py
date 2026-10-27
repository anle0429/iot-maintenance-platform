"""Bind completed training and data-version source to Git blobs before model registration."""
import argparse,json,subprocess,sys
from pathlib import Path
from hashlib import sha256
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.training_contract import save


def run(args):
    """Refuse a source commit that does not contain the exact training execution bytes."""
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    preflight=json.loads(args.training_preflight.read_text());files={}
    names=[*preflight['code_sha256'],'ml/canonical_dataset.py',
        'ml/data_versions.py','ml/mlflow_data_versions.py','scripts/version-feast-dataset.py']
    for name in sorted(set(names)):
        blob=subprocess.check_output(['git','show',commit+':'+name],cwd=ROOT);h=sha256(blob).hexdigest()
        if name in preflight['code_sha256'] and h!=preflight['code_sha256'][name]:raise ValueError('Commit differs from actual training: '+name)
        if sha256((ROOT/name).read_bytes()).hexdigest()!=h:raise ValueError('Working copy differs from named commit: '+name)
        files[name]=h
    save(args.output,{'format':'iot-source-snapshot-v1','commit':commit,'files':files,
        'scope':'Git-derived training and data-version source subset. Registry logging code has a separate receipt hash. A VM may execute these exact snapshot bytes without a Git checkout.'})
    print(json.dumps({'commit':commit,'files':len(files)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--training-preflight',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);run(parser.parse_args())
