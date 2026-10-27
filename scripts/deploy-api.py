"""Deploy one private coursework API with Helm3 atomic rollback and saved native command output."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]


def run(args):
    """Lint first, then require readiness; an unsuccessful upgrade keeps a nonzero exit and native rollback log."""
    if args.output.exists():
        raise ValueError('Choose a new deployment receipt directory')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', args.image_tag) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', args.revision):
        raise ValueError('Invalid image tag or revision marker')
    if args.feature_seed is not None and (args.api != 'feature' or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', args.feature_seed)):
        raise ValueError('A feature seed must name a valid dedicated ConfigMap for the feature API')
    args.output.mkdir(parents=True)
    chart = ROOT/'infra/charts/iot-api'
    values = ROOT/('infra/'+args.api+'-api/helm-values.yaml')
    base = [args.helm]
    version = subprocess.check_output(base+['version','--short'], text=True).strip()
    if not version.startswith('v3.'):
        raise ValueError('This deployment command was designed and checked for Helm3 --atomic')
    with (args.output/'lint.txt').open('w') as log:
        subprocess.run(base+['lint',str(chart),'-f',str(values)],stdout=log,stderr=subprocess.STDOUT,check=True)
    command = base+['upgrade','--install','iot-'+args.api+'-api',str(chart),'-n','iot-features',
                    '-f',str(values),'--atomic','--wait','--timeout',str(args.timeout_seconds)+'s',
                    '--set-string','image.tag='+args.image_tag,'--set-string','revisionMarker='+args.revision,
                    '--set','autoscaling.enabled='+str(args.autoscaling).lower()]
    if args.feature_seed is not None:
        command += ['--set-string', 'volumes[0].configMap.name='+args.feature_seed]
    with (args.output/'helm-console.txt').open('w') as log:
        completed = subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=args.timeout_seconds*2+120)
    (args.output/'receipt.json').write_text(json.dumps({'passed':completed.returncode==0,'exit_code':completed.returncode,
         'api':args.api,'image_tag':args.image_tag,'revision':args.revision,'helm_version':version,
         'feature_seed_configmap':args.feature_seed,
         'command':command,'completed_utc':datetime.now(timezone.utc).isoformat(),
         'scope':'Native Helm3 readiness-gated deployment. A failed command is not evidence of a successful fallback until release/pod/HTTP state is read back.'},indent=2)+'\n')
    return completed.returncode


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api',choices=['feature','drift'],required=True)
    parser.add_argument('--helm',default='helm');parser.add_argument('--image-tag',default='v1')
    parser.add_argument('--revision',required=True);parser.add_argument('--autoscaling',action='store_true')
    parser.add_argument('--feature-seed',help='Fresh checksum-bound serving seed ConfigMap; feature API only')
    parser.add_argument('--timeout-seconds',type=int,default=180)
    parser.add_argument('--output',type=Path,required=True)
    sys.exit(run(parser.parse_args()))
