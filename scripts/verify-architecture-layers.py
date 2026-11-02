"""Verify the seven architecture views, implementation bindings and selected deployment coverage offline."""
from pathlib import Path
from hashlib import sha256
import argparse
import json
import subprocess
from documentation_presentation import ROOT, presentation

LAYERS_SHA256='196f441a79bff4c628374ce6b640908ef536f5042eff6bd7f374647937c30e09'
PRIMARY=['architecture-system','flow-batch','flow-stream','flow-training','flow-prediction','flow-agents','deployment-runtime']
COUNTS=[(22,25),(10,9),(9,9),(12,12),(10,11),(13,21),(18,9)]


def canonical(path):
    """Normalize only Windows text newlines; PNG/GIF identities are checked as exact bytes."""
    body=path.read_bytes()
    return body if path.suffix in ['.png','.gif'] else body.replace(b'\r\n',b'\n')


def semantic_boundaries(scenes,groups):
    """Independently require the storage, training, agent and runtime connections that must not be conflated."""
    critical={
      'architecture-system':{'F0':('sys-history','sys-batchredis'),'F1':('sys-history','sys-prep'),
        'S3':('sys-writers','sys-streamredis'),'F2':('sys-prep','sys-servingredis'),'K1':('sys-rag','sys-mcp'),
        'T1':('sys-training','sys-ci'),'C1':('sys-ci','sys-model'),'R1':('sys-ops','sys-training')},
      'flow-batch':{'B6':('batch-dp3','batch-features'),'B7':('batch-dp3','batch-labels')},
      'flow-stream':{'S6':('stream-offline','stream-history'),'S7':('stream-history','stream-online'),
        'S8':('stream-online','stream-redis'),'S9':('stream-dedup','stream-results')},
      'flow-training':{'T1':('train-features','train-load'),'T2':('train-labels','train-load'),
        'F0':('train-features','train-batch-online'),'T6':('train-validate','train-evaluate'),
        'T7':('train-evaluate','train-registry'),'C1':('train-registry','train-ci'),'C2':('train-ci','train-serving'),
        'R1':('train-admission','train-load')},
      'flow-prediction':{'U4':('predict-sdk','predict-redis'),'U5':('predict-redis','predict-sdk'),
        'U8':('predict-check','predict-model'),'U9':('predict-model','predict-result-check'),
        'U10':('predict-result-check','predict-response'),'U11':('predict-result-check','predict-reject')},
      'flow-agents':{'G1':('agent-coordinator','agent-router'),'G2':('agent-retrieval','agent-router'),
        'G3':('agent-drift','agent-router'),'M4':('agent-drift-mcp','agent-feature-api'),
        'M5':('agent-drift-mcp','agent-drift-api'),'G4':('agent-router','agent-observer'),
        'G5':('agent-observer','agent-gpu'),'G6':('agent-gpu','agent-observer'),
        'G7':('agent-observer','agent-router')},
      'deployment-runtime':{'U2':('deploy-iap','deploy-viewer'),'U3':('deploy-viewer','deploy-gateway'),
        'G1':('deploy-llm-cpu','deploy-model'),'G2':('deploy-model','deploy-llm-cpu'),
        'D1':('deploy-volumes','deploy-kfp'),'D2':('deploy-volumes','deploy-jobs')},
    }
    for name,pairs in critical.items():
        arrows={e['id']:e for e in scenes[name]['elements'] if e['type']=='arrow'}
        for key,(start,end) in pairs.items():
            edge=arrows[key]
            assert (edge['customData']['from'],edge['customData']['to'])==(start,end),(name,key)
            assert edge['startBinding']['elementId']==start and edge['endBinding']['elementId']==end
    old=json.loads((ROOT/'docs/diagrams/platform-deployment.excalidraw').read_bytes())
    members=[name for values in groups.values() for name in values]
    expected={e['id'] for e in old['elements'] if e.get('customData',{}).get('node')}
    assert len(members)==len(set(members))==len(expected)==42 and set(members)==expected
    assert groups['deploy-model']==['gpu'] and set(groups['deploy-llm-cpu'])=={'router','observer'}
    assert set(groups['deploy-workers'])=={'retrieval','drift-pool','coordinator'}
    deployment={e['id']:e for e in scenes['deployment-runtime']['elements']}
    locations={
      'deploy-pc-region':['deploy-browser','deploy-iap'],
      'deploy-gpu-region':['deploy-model'],
      'deploy-k3s-region':['deploy-kfp','deploy-serving','deploy-control','deploy-workers','deploy-mcp','deploy-substrate','deploy-llm-cpu','deploy-gateway','deploy-jaeger'],
      'deploy-outside-region':['deploy-airflow','deploy-ci','deploy-jobs','deploy-host'],
      'deploy-cpu-region':['deploy-viewer','deploy-volumes'],
    }
    for frame,children in locations.items():
        box=deployment[frame]
        for child in children:
            item=deployment[child]
            assert box['x']<=item['x'] and box['y']<=item['y'] and item['x']+item['width']<=box['x']+box['width'] and item['y']+item['height']<=box['y']+box['height'],(frame,child)
    return sum(len(v) for v in critical.values())


def check():
    """Authenticate exact authored assets and source text while retaining all original evidence and reference artwork."""
    body=canonical(ROOT/'docs/diagrams/layers.json')
    assert sha256(body).hexdigest()==LAYERS_SHA256,'Architecture crosswalk changed'
    value=json.loads(body);base=value['base_commit']
    assert base=='8ed671f0f0bdbd6fa2a9bb755196163313dfede0'
    assert value['layers']=={'1':PRIMARY[:1],'2':PRIMARY[1:6],'3':PRIMARY[6:]}
    display=presentation();index=json.loads((ROOT/'docs/images/diagram-index.json').read_bytes())
    old_index=json.loads(subprocess.check_output(['git','show',base+':docs/images/diagram-index.json'],cwd=ROOT))
    assert index['views'][:11]==old_index['views'] and [v['name'] for v in index['views'][11:]]==PRIMARY
    scenes={name:json.loads((ROOT/'docs/diagrams'/(name+'.excalidraw')).read_bytes()) for name in PRIMARY}
    for name,(nodes,edges) in zip(PRIMARY,COUNTS):
        scene=scenes[name];row=value['views'][name]
        assert scene['customData']['layer']==row['layer']
        actual_nodes=[e['id'] for e in scene['elements'] if e.get('customData',{}).get('node')]
        actual_edges={e['id']:{k:e['customData'][k] for k in ['from','to','label','kind','sources','note']} for e in scene['elements'] if e['type']=='arrow'}
        assert actual_nodes==row['nodes'] and actual_edges==row['edges']
        assert (len(actual_nodes),len(actual_edges))==(nodes,edges)
        assert min(e['fontSize'] for e in scene['elements'] if e['type']=='text')>=22
        for path in ['docs/diagrams/'+name+'.excalidraw',*['docs/images/'+name+suffix for suffix in ['.png','.svg','.animated.svg','.gif','.preview.json']]]:
            assert sha256(canonical(ROOT/path)).hexdigest()==display['assets'][path]['sha256'],path
    boundaries=semantic_boundaries(scenes,value['deployment_group_members'])
    for name,pin in value['source_sha256'].items():assert sha256(canonical(ROOT/name)).hexdigest()==pin,name
    for name,fragments in value['critical_source_fragments'].items():
        text=(ROOT/name).read_text(encoding='utf-8')
        for part in fragments:assert part in text,(name,part)
    agents=(ROOT/'infra/charts/iot-agents/templates/agents.yaml').read_text(encoding='utf-8')
    assert agents.count('modelConfig: {{ .Values.modelConfig }}')==3
    preserved=0
    for folder in ['docs/images','docs/diagrams']:
        originals=subprocess.check_output(['git','ls-tree','-r','--name-only',base,'--',folder],cwd=ROOT).decode().splitlines()
        for name in originals:
            if name=='docs/images/diagram-index.json' or folder=='docs/diagrams' and not name.endswith('.excalidraw'):continue
            assert canonical(ROOT/name)==subprocess.check_output(['git','show',base+':'+name],cwd=ROOT),name
            preserved+=1
    for tree in ['docs/evidence','docs/evidence-archive','docs/rubric','references','knowledge']:
        assert not subprocess.check_output(['git','diff',base,'--name-only','--',tree],cwd=ROOT).strip(),tree
    return {'passed':True,'primary_views':7,'layers':{'system':1,'flows':5,'deployment':1},
      'primary_nodes':94,'primary_directed_relationships':96,'critical_endpoint_checks':boundaries,
      'bound_source_files':len(value['source_sha256']),'selected_deployment_roles_covered':42,'host_runtime_placement_checked':True,
      'original_reference_image_and_native_files_unchanged':preserved,'new_authored_artifacts':42,
      'scope':'Offline authored geometry/source/deployment correspondence; no new runtime proof, formal certification or grade.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path);args=parser.parse_args()
    report=check()
    if args.output:
        target=args.output.resolve()
        if not target.is_relative_to(ROOT/'outputs') or target.exists():raise ValueError('Use a fresh receipt under outputs/')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))
