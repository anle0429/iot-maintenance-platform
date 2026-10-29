"""Validate immutable model identity pins shared by the predictor and feature API."""
import math
import re
from ml.protocol import PROTOCOL_SHA256

MODEL='iot-pump-failure-v1'
FIELDS=('name','version','run_id','protocol_sha256','portable_sha256','training_code_commit','data_version','threshold','production')
LEGACY={'name':MODEL,'version':'1','run_id':'c629f87591294157a6b30040c8c12715',
    'protocol_sha256':PROTOCOL_SHA256,'portable_sha256':'699eb9d0d831960fe7eb9bc3f8719f88af530881543bd3ee5245d8537c9f2bc9',
    'training_code_commit':'cbcb1edae4df9c9af4198b6685ee9d184d9d1e6e',
    'data_version':'29f0c3c7b3af396a876b40ca0fea9d5648efc22edcee2f8273b5f30715af1466','threshold':0.7,'production':False}


def validate_pin(value):
    """Require a complete typed pin; an empty or partial explicit config never falls back to legacy."""
    if type(value) is not dict or set(value)!=set(FIELDS):raise ValueError('Model pin fields are incomplete or unexpected')
    if value['name']!=MODEL or value['protocol_sha256']!=PROTOCOL_SHA256:raise ValueError('Model pin name or protocol differs')
    for name,pattern in [('version',r'[1-9][0-9]*'),('run_id',r'[0-9a-f]{32}'),
            ('portable_sha256',r'[0-9a-f]{64}'),('training_code_commit',r'[0-9a-f]{40}'),('data_version',r'[0-9a-f]{64}')]:
        if not isinstance(value[name],str) or not re.fullmatch(pattern,value[name]):raise ValueError('Invalid model pin '+name)
    threshold=value['threshold']
    if type(threshold) not in [int,float] or not math.isfinite(threshold) or not 0<threshold<1:
        raise ValueError('Invalid pinned model threshold')
    if type(value['production']) is not bool or (not value['production'] and value['version']!='1'):
        raise ValueError('A version beyond legacy version1 requires a production registry release')
    return dict(value)


def from_lineage(lineage,threshold):
    """Read the same pin from the hash-checked portable bundle and its registry export receipt."""
    pin={n:lineage['model_name' if n=='name' else n] for n in FIELDS if n not in ['threshold','production']}
    pin.update(threshold=threshold,production=lineage.get('production',False));pin=validate_pin(pin)
    if pin['production']:
        selected=lineage.get('registry_selection',{})
        if type(selected) is not dict:raise ValueError('Invalid production registry selection')
        if (selected.get('name')!=pin['name'] or selected.get('version')!=pin['version'] or selected.get('run_id')!=pin['run_id']
                or selected.get('registry_stage')=='Archived'
                or not (selected.get('production_tag')=='true' or (selected.get('production_tag') is None and selected.get('registry_stage')=='Production'))
                or lineage.get('registry_uri')!='models:/'+MODEL+'/'+pin['version']):
            raise ValueError('Model bundle lacks its matching production registry selection')
    return pin
