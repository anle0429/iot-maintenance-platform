"""Generate an isolated private host monitoring configuration; no exporter or service is installed or started."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ml.host_monitoring import write_bundle


def main():
    """Keep the observed endpoint, host alias, OS and evidence scope explicit."""
    parser=argparse.ArgumentParser(description=__doc__)
    for arg in ['output','endpoint','family','host','label']:parser.add_argument('--'+arg,required=True,**({'choices':['windows','linux']} if arg=='family' else {}))
    parser.add_argument('--prometheus-port',type=int,default=19091);parser.add_argument('--grafana-port',type=int,default=13001)
    a=parser.parse_args();print(json.dumps(write_bundle(a.output,a.endpoint,a.family,a.host,a.label,a.prometheus_port,a.grafana_port),indent=2))


if __name__=='__main__':main()
