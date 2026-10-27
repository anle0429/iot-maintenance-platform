"""Seal the exact committed source used by automatic versioning before copying it to the prepared VM."""
import argparse,json,subprocess,sys
from pathlib import Path
from hashlib import sha256
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.versioned_contract import SOURCE_FILES
from ml.training_contract import save


def run(args):
    """Reject uncommitted code and record only technical source hashes, private registry address and parent data version."""
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip();files={}
    for name in SOURCE_FILES:
        blob=subprocess.check_output(['git','show',commit+':'+name],cwd=ROOT);h=sha256(blob).hexdigest()
        if sha256((ROOT/name).read_bytes()).hexdigest()!=h:raise ValueError('Working source differs from commit: '+name)
        files[name]=h
    if args.output.exists():raise ValueError('Preserve previous source binding')
    save(args.output,{'format':'iot-source-snapshot-v1','commit':commit,'files':files,'parent_data_version':args.parent_version,
        'tracking_uri':'http://tracking:5000','scope':'Exact committed execution source; VM may run a copied snapshot, no inferred runtime Git HEAD.'})
    print(json.dumps({'commit':commit,'files':len(files)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--parent-version');run(parser.parse_args())
