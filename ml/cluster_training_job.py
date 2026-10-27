"""Persist typed input descriptors across KFP pods before executing the unchanged Spark training implementation."""
from pathlib import Path
import json,sys,runpy


def persistent_input(arguments):
    """Keep byte identity while removing references to a prior pod's ephemeral artifact download path."""
    args=list(arguments);step=args[args.index('--step')+1];source=Path(args[args.index('--input')+1]);workspace=Path(args[args.index('--workspace')+1])
    expected={'split':'feast-cohort','train':'temporal-split','evaluate':'validation-selected-model'}[step]
    content=source.read_bytes();value=json.loads(content)
    if not value.get('passed') or value.get('kind')!=expected:raise ValueError('Unexpected typed training input descriptor')
    folder=workspace/'descriptors';folder.mkdir(parents=True,exist_ok=True)
    target=folder/(step+'-input.json')
    with target.open('xb') as stream:stream.write(content)
    args[args.index('--input')+1]=str(target)
    return args


if __name__=='__main__':
    sys.argv=[sys.argv[0],*persistent_input(sys.argv[1:])]
    runpy.run_module('ml.training_job',run_name='__main__')
