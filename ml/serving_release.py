"""Export an explicitly production-marked, immutable registry version into a hash-bound serving image bundle."""
from datetime import datetime,timezone
from hashlib import sha256
import json
import math
from pathlib import Path,PurePosixPath
import re
import shutil
import tempfile

from ml.protocol import PROTOCOL_SHA256,load_protocol

MODEL='iot-pump-failure-v1'
HEX64=re.compile(r'[0-9a-f]{64}')


def authorized_version(version,expected):
    """Require one pinned ready version and an explicit production deployment marker."""
    if not isinstance(expected,str) or not re.fullmatch(r'[1-9][0-9]*',expected):
        raise ValueError('Pin a canonical positive model version')
    if version.name!=MODEL or str(version.version)!=expected or version.status!='READY':
        raise ValueError('Registry identity or readiness differs from the pinned release')
    marker=version.tags.get('production')
    stage=getattr(version,'current_stage',None)
    if stage=='Archived' or (marker!='true' and not (marker is None and stage=='Production')):
        raise ValueError('Model version is not explicitly marked production')
    if not re.fullmatch(r'[0-9a-f]{32}',version.run_id or ''):
        raise ValueError('Require the actual completed source run')
    return {'name':MODEL,'version':expected,'run_id':version.run_id,
        'production_tag':marker,'registry_stage':stage,'source':version.source}


def _sha(raw):
    """Hash exact downloaded bytes before decoding any descriptor."""
    return sha256(raw).hexdigest()


def _read(client,run_id,path,temporary,size=None):
    """Keep registry downloads inside a fresh temporary directory and reject large evidence blobs."""
    if size is not None and (type(size) is not int or not 0<size<=1_000_000):
        raise ValueError('Lineage artifact size is missing or outside the bounded release limit')
    name=PurePosixPath(path)
    if name.is_absolute() or '..' in name.parts or '\\' in path:
        raise ValueError('Unsafe registry artifact path')
    actual=Path(client.download_artifacts(run_id,path,str(temporary))).resolve()
    if not actual.is_relative_to(temporary.resolve()) or not actual.is_file() or not 0<actual.stat().st_size<=1_000_000:
        raise ValueError('Registry download escaped its bounded temporary directory')
    raw=actual.read_bytes()
    if size is not None and len(raw)!=size:
        raise ValueError('Downloaded artifact size differs from registry metadata')
    return raw


def export(client,version,destination,protocol_path):
    """Pull production bytes, verify the descriptor chain, then publish a complete new bundle atomically."""
    destination=Path(destination)
    if destination.exists():raise FileExistsError('Preserve the previous serving release bundle')
    before=authorized_version(client.get_model_version(MODEL,version),version)
    run=client.get_run(before['run_id']);tags=dict(run.data.tags)
    if run.info.status!='FINISHED' or tags.get('protocol_sha256')!=PROTOCOL_SHA256:
        raise ValueError('Model source run is unfinished or uses another protocol')
    keys=['evaluation_sha256','selection_sha256','training_data_version']
    if any(not HEX64.fullmatch(tags.get(k,'')) for k in keys):
        raise ValueError('Model run lacks sealed evaluation, selection or data identity')
    commits=[tags[k] for k in ['source_snapshot_commit','code_git_commit'] if k in tags]
    if not commits or len(set(commits))!=1 or not re.fullmatch(r'[0-9a-f]{40}',commits[0]):
        raise ValueError('Training source commit is missing or inconsistent')
    protocol=load_protocol(Path(protocol_path))
    with tempfile.TemporaryDirectory() as directory:
        temporary=Path(directory)
        portable_files=client.list_artifacts(before['run_id'],'portable')
        if (len(portable_files)!=1 or portable_files[0].is_dir
                or portable_files[0].path!='portable/portable.json'):
            raise ValueError('Require one bounded portable serving artifact')
        portable=_read(client,before['run_id'],'portable/portable.json',temporary,portable_files[0].file_size)
        files=client.list_artifacts(before['run_id'],'lineage')
        if not 1<=len(files)<=32:raise ValueError('Unexpected lineage artifact inventory')
        descriptors={};paths={}
        for item in files:
            if item.is_dir or PurePosixPath(item.path).parent!=PurePosixPath('lineage') or not item.path.endswith('.json'):
                raise ValueError('Require a flat JSON lineage inventory')
            raw=_read(client,before['run_id'],item.path,temporary,item.file_size)
            h=_sha(raw)
            descriptors[h]=json.loads(raw);paths.setdefault(h,[]).append(item.path)
        evaluation=descriptors.get(tags['evaluation_sha256'],{})
        selected=descriptors.get(tags['selection_sha256'],{})
        if (not evaluation.get('passed') or evaluation.get('kind')!='holdout-evaluation'
                or evaluation.get('protocol_sha256')!=PROTOCOL_SHA256
                or evaluation.get('selection_sha256')!=tags['selection_sha256']
                or not selected.get('passed') or selected.get('kind')!='validation-selected-model'
                or selected.get('protocol_sha256')!=PROTOCOL_SHA256
                or selected.get('scaler_fit_partition')!='train'
                or selected.get('threshold_selection_partition')!='validation' or selected.get('holdout_rows_opened')!=0):
            raise ValueError('Evaluation and selection do not form the sealed training chain')
        partitions=descriptors.get(selected.get('split_descriptor_sha256'),{})
        if not partitions.get('passed') or partitions.get('kind')!='temporal-split' or partitions.get('protocol_sha256')!=PROTOCOL_SHA256:
            raise ValueError('Temporal split descriptor is missing or altered')
        portable_path=PurePosixPath(selected['portable_path']);bound=[]
        for artifact in selected['artifacts']:
            parent=PurePosixPath(artifact['path'])
            if portable_path.is_relative_to(parent):
                name=portable_path.relative_to(parent).as_posix()
                if name in artifact['files']:bound.append(artifact['files'][name])
        if bound!=[_sha(portable)]:raise ValueError('Registry portable bytes differ from evaluated model inventory')
        model=json.loads(portable)
        arrays=[model.get(n,[]) for n in ['mean','std','coefficients']]
        if (any(not isinstance(values,list) or len(values)!=8 or any(type(v) not in [int,float] or not math.isfinite(v) for v in values) for values in arrays)
                or any(v<0 for v in model['std'])
                or type(model.get('intercept')) not in [int,float] or not math.isfinite(model['intercept'])
                or type(model.get('threshold')) not in [int,float] or not 0<model['threshold']<1):
            raise ValueError('Portable model contains invalid numeric metadata')
        if (model.get('kind')!='spark-standardscaler-logistic-portable-v1'
                or model.get('protocol_sha256')!=PROTOCOL_SHA256 or model.get('features')!=protocol['feature_allowlist']
                or model.get('threshold')!=evaluation['fixed_threshold'] or model.get('threshold')!=selected['validation']['threshold']):
            raise ValueError('Serving model feature/protocol/threshold contract differs')
        data_run_id=tags.get('dataset_run_id')
        if data_run_id:
            if not re.fullmatch(r'[0-9a-f]{32}',data_run_id):raise ValueError('Invalid incremental data run identity')
            matches=[d for d in descriptors.values() if d.get('data_version')==tags['training_data_version'] and d.get('data_run_id')==data_run_id]
            if len(matches)!=1 or matches[0].get('native_report_sha256')!=partitions['native_report_sha256']:
                raise ValueError('Incremental data reference differs from the model split')
            data_run=client.get_run(data_run_id)
            if data_run.info.status!='FINISHED' or data_run.data.tags.get('data_version')!=tags['training_data_version']:
                raise ValueError('Incremental data run is not sealed')
            manifest=_read(client,data_run_id,'dataset-version/manifest.json',temporary)
            if _sha(manifest)!=tags['training_data_version']:raise ValueError('Incremental data manifest bytes changed')
        elif partitions['native_report_sha256']!=tags['training_data_version']:
            raise ValueError('Legacy full-run data identity differs from the model split')
        if authorized_version(client.get_model_version(MODEL,version),version)!=before:
            raise ValueError('Production marker or model source changed while exporting')
        after=client.get_run(before['run_id'])
        if after.info.status!='FINISHED' or dict(after.data.tags)!=tags:
            raise ValueError('Model source run changed while exporting')
        lineage={'model_name':MODEL,'version':version,'run_id':before['run_id'],'production':True,
            'deployment_scope':'synthetic-coursework-private-environment','training_code_commit':commits[0],
            'data_version':tags['training_data_version'],'dataset_run_id':data_run_id,
            'evaluation_sha256':tags['evaluation_sha256'],'selection_sha256':tags['selection_sha256'],
            'protocol_sha256':PROTOCOL_SHA256,'portable_sha256':_sha(portable),
            'registry_uri':'models:/'+MODEL+'/'+version,'registry_selection':before,
            'source_artifacts':paths,'exported_utc':datetime.now(timezone.utc).isoformat(),
            'scope':'Explicit registry production marker for a private synthetic coursework release, not a real-equipment safety or calibration claim.'}
        destination.parent.mkdir(parents=True,exist_ok=True)
        stage=Path(tempfile.mkdtemp(prefix=destination.name+'-pending-',dir=destination.parent))
        try:
            (stage/'portable.json').write_bytes(portable)
            (stage/'lineage.json').write_text(json.dumps(lineage,indent=2)+'\n',encoding='utf-8')
            if destination.exists():raise FileExistsError('Serving bundle appeared during export')
            stage.rename(destination)
        except BaseException:
            if stage.resolve().parent!=destination.parent.resolve() or not stage.name.startswith(destination.name+'-pending-'):
                raise RuntimeError('Refuse cleanup outside the created release staging directory')
            shutil.rmtree(stage);raise
    return lineage
