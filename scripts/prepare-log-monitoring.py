"""Prepare a fresh private Linux log-monitoring bundle from explicit observed host and Pod source identities."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ml.log_monitoring import write_bundle

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--project-root',required=True)
    parser.add_argument('--sources',type=Path,required=True);parser.add_argument('--host',default='vm-linux')
    args=parser.parse_args();write_bundle(args.output,args.project_root,json.loads(args.sources.read_text()),args.host)
    print(json.dumps({'prepared':True,'output':str(args.output),'services_started':False}))
