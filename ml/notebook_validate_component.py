"""Publish the notebook's validation-reload check as a typed KFP artifact."""
import argparse
from pathlib import Path
from ml.notebook_validate import run
from ml.kfp_artifacts import publish


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for n in ['selection','protocol','output']:parser.add_argument('--'+n,type=Path,required=True)
    parser.add_argument('--executor-input',required=True);args=parser.parse_args();run(args)
    publish(args.executor_input,'verification',args.output)
