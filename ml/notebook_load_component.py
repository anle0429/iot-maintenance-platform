"""Resolve a workspace subdirectory in Python instead of a backend-sensitive KFP Concat placeholder."""
import argparse
from pathlib import Path
from ml.notebook_load import run
from ml.kfp_artifacts import publish


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['workspace','output','protocol','state','artifacts','batch-report','feature-report']:
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--executor-input',required=True);args=parser.parse_args()
    args.workspace=args.workspace/'input';run(args);publish(args.executor_input,'cohort',args.output)
