"""Reject stale, mismatched or production-promoting results before training CI can succeed."""
from datetime import datetime,timezone
import re


def identity(build):
    """Create a fresh filesystem/KFP identity from a canonical bounded build number."""
    if not isinstance(build,str) or not re.fullmatch(r'[1-9][0-9]{0,9}',build):
        raise ValueError('Use a canonical positive Jenkins build number')
    return 'train-jenkins-'+build


def admission(stop,now,ram,disks):
    """Keep the original full-run90minute/24GiB/40GB admission unchanged."""
    deadline=datetime.fromisoformat(stop.replace('Z','+00:00'))
    if deadline.tzinfo is None or now.tzinfo is None:
        raise ValueError('Use explicit UTC audit clocks')
    remaining=(deadline-now).total_seconds()
    value={'passed':remaining>=5400 and ram>=24*1024**3 and len(disks)==2 and min(disks)>=40_000_000_000,
        'seconds_before_actual_stop':remaining,'ram_available_bytes':ram,'disk_free_bytes':disks,
        'required_seconds':5400,'required_ram_bytes':24*1024**3,'required_disk_bytes':40_000_000_000,
        'checked_utc':now.astimezone(timezone.utc).isoformat(),'auto_stop_utc':stop}
    if not value['passed']:
        raise ValueError('Full training CI needs90minutes,24GiB RAM and40GB on both data disks')
    return value


def verify_result(preflight,submission,verification,data,model,commit,started):
    """Bind this new submission, execution source, data pull and model candidate together."""
    if datetime.fromisoformat(preflight['checked_utc'])<datetime.fromisoformat(started):
        raise ValueError('A previous training run cannot satisfy this CI build')
    if not (verification['passed'] and submission['run_id']==verification['run_id']
            and preflight['source_snapshot_commit']==commit==model['source_snapshot_commit']==data['source_snapshot_commit']
            and data['passed'] and model['passed'] and model['production'] is False and verification['production'] is False
            and model['version']==verification['registered_model_version']
            and model['model_run_id']==verification['model_run_id']
            and data['data_version']==verification['data_version']==model['incremental_data_reference']['data_version']
            and data['data_run_id']==verification['data_run_id']==model['incremental_data_reference']['data_run_id']):
        raise ValueError('Fresh CI source/data/model lineage does not agree')
    return {'passed':True,'run_id':submission['run_id'],'model_version':model['version'],
        'data_version':data['data_version'],'production':False}
