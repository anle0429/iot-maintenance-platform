"""Download only an explicit production-marked MLflow version and produce a sealed serving build asset."""
import argparse,json,sys
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.serving_release import export


def run(args):
    """Use the existing private registry; credentials belong to SDK environment configuration, never arguments."""
    address=urlsplit(args.tracking_uri)
    if address.username or address.password or address.query or address.fragment:
        raise ValueError('Tracking URI must not contain credentials or query parameters')
    if address.scheme not in ['http','https'] or not address.hostname:raise ValueError('Use an explicit registry HTTP address')
    from mlflow.tracking import MlflowClient
    lineage=export(MlflowClient(tracking_uri=args.tracking_uri),args.version,args.output,args.protocol)
    print(json.dumps({'passed':True,'version':lineage['version'],'run_id':lineage['run_id'],
        'portable_sha256':lineage['portable_sha256'],'production':True,'scope':lineage['deployment_scope']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tracking-uri',required=True);parser.add_argument('--version',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--protocol',type=Path,default=ROOT/'ml/protocols/medium-60d-v1.json')
    run(parser.parse_args())
