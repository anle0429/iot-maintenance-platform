"""Prepare an isolated monitoring runtime for an observed private A/B API; no service is started."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ml.ab_monitoring import write_bundle


def main():
    """Require an explicit endpoint, experiment and scope label so fixture/native evidence stays distinct."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True);parser.add_argument('--endpoint',required=True)
    parser.add_argument('--experiment',required=True);parser.add_argument('--label',required=True)
    parser.add_argument('--prometheus-port',type=int,default=19090);parser.add_argument('--grafana-port',type=int,default=13000)
    args=parser.parse_args()
    print(json.dumps(write_bundle(args.output,args.endpoint,args.experiment,args.label,args.prometheus_port,args.grafana_port),indent=2))


if __name__=='__main__':main()
