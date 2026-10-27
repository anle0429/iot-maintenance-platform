"""Derive the feature API's expected identity from the actual validated predictor bundle."""
import argparse,json,sys
from hashlib import sha256
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from services.predictor.runtime import PumpPredictor


def run(args):
    """Preserve an existing pin file; legacy bundles remain explicitly production=false."""
    predictor=PumpPredictor(args.bundle)
    raw=(json.dumps(predictor.pin,indent=2)+'\n').encode()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('xb') as target:target.write(raw)
    print(json.dumps({'version':predictor.pin['version'],'production':predictor.pin['production'],
        'portable_sha256':predictor.pin['portable_sha256'],'pin_sha256':sha256(raw).hexdigest()}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args())
