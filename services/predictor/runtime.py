"""Load an immutable model-inside-image bundle and retain source-to-prediction lineage."""

from hashlib import sha256
import json,math
from pathlib import Path

from ml.protocol import PROTOCOL_SHA256
from ml.training_contract import score_portable
from services.predictor.contracts import FeatureVector


class PumpPredictor:
    """Read the validated Spark model once; no runtime training, network download or label inputs."""

    def __init__(self,bundle:Path):
        """Fail startup if portable bytes, schema or pinned lineage are inconsistent."""
        raw=(bundle/'portable.json').read_bytes()
        self.lineage=json.loads((bundle/'lineage.json').read_text())
        self.model=json.loads(raw)
        if sha256(raw).hexdigest()!=self.lineage['portable_sha256']:
            raise ValueError('Model artifact differs from its sealed lineage')
        if self.model.get('kind')!='spark-standardscaler-logistic-portable-v1':
            raise ValueError('Unsupported model format')
        if self.model['protocol_sha256']!=PROTOCOL_SHA256 or self.lineage['protocol_sha256']!=PROTOCOL_SHA256:
            raise ValueError('Model protocol differs from the frozen training protocol')
        if self.model['features']!=list(FeatureVector.model_fields):
            raise ValueError('Serving schema differs from the eight training inputs')
        arrays=[self.model[n] for n in ['mean','std','coefficients']]
        if any(len(a)!=8 or any(type(v) not in [int,float] or not math.isfinite(v) for v in a) for a in arrays):
            raise ValueError('Model scaler/coefficients are invalid')
        if any(v<0 for v in self.model['std']) or not math.isfinite(self.model['intercept']) or not 0<self.model['threshold']<1:
            raise ValueError('Model numeric metadata is invalid')
        if self.lineage['model_name']!='iot-pump-failure-v1' or self.lineage['version']!='1':
            raise ValueError('This image is bound to evaluated model version1')

    def predict(self,instances:list[FeatureVector])->list[dict]:
        """Score in recorded feature order with the validation-selected threshold unchanged."""
        result=[]
        for row in instances:
            score=score_portable([getattr(row,n) for n in self.model['features']],self.model)
            result.append({'risk_score':score,'predicted_failure':int(score>=self.model['threshold'])})
        return result

    def metadata(self)->dict:
        """Return machine-readable provenance without suggesting calibrated real-world probability."""
        return {'name':self.lineage['model_name'],'version':self.lineage['version'],
            'run_id':self.lineage['run_id'],'protocol_sha256':PROTOCOL_SHA256,
            'portable_sha256':self.lineage['portable_sha256'],'training_code_commit':self.lineage['training_code_commit'],
            'data_version':self.lineage['data_version'],'features':self.model['features'],
            'threshold':self.model['threshold'],'score_kind':'uncalibrated_class_weighted_logistic',
            'data_scope':'synthetic-medium-60d','production':False}
