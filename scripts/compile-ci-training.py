"""Compile the unchanged five-task pipeline and bind every task to its tested release image."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def run(manifest,output):
    """Change only SDK image references; retain steps, commands, resource limits and the frozen protocol."""
    import yaml
    from kfp import compiler
    from ml.kfp_versioned import versioned_pipeline
    images=json.loads(manifest.read_text())['images']
    compiler.Compiler().compile(versioned_pipeline,str(output))
    documents=list(yaml.safe_load_all(output.read_text()));package=documents[0]
    mapping={'iot-coursework-feast-versioned:0.67.0-3.17.2':images['feast']['tag'],
        'iot-coursework-spark:3.5.9':images['spark']['tag'],
        'iot-coursework-spark-registry:3.5.9-3.17.2':images['spark']['tag']}
    executors=package['deploymentSpec']['executors']
    if len(executors)!=5 or len(package['root']['dag']['tasks'])!=5:
        raise ValueError('Training CI requires the same five real ML stages')
    for executor in executors.values():
        container=executor['container']
        if container['image'] not in mapping:
            raise ValueError('Unexpected runtime image in compiled pipeline')
        container['image']=mapping[container['image']]
    for task in package['root']['dag']['tasks'].values():
        if task.get('cachingOptions',{}).get('enableCache',False):
            raise ValueError('CI training must execute fresh components')
    output.write_text(yaml.safe_dump_all(documents,sort_keys=False),encoding='utf-8')
    print(json.dumps({'passed':True,'tasks':5,'images':sorted({x['container']['image'] for x in executors.values()})}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.manifest,args.output)
