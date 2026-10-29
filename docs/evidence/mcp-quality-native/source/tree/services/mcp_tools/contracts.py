"""Define strict tool inputs and the provenance required from a native machine-feature read."""
from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, AwareDatetime, model_validator
from services.predictor.contracts import FeatureVector

MACHINE_PATTERN = r'^P-[0-9]{3,4}$'
MachineId = Annotated[str, StringConstraints(pattern=MACHINE_PATTERN)]
Digest = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]


class MachineRequest(BaseModel):
    """Select one machine without accepting a URL, model override or feature values."""
    model_config = ConfigDict(extra='forbid', strict=True)
    machine_id: MachineId


class SearchRequest(BaseModel):
    """Bound retrieval context before encoding or querying the native feature store."""
    model_config = ConfigDict(extra='forbid', strict=True)
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=1000)]
    top_k: Annotated[int, Field(ge=1, le=3)] = 3


class DriftRequest(BaseModel):
    """Select a unique bounded cohort and immutable assessment identity; labels are never accepted."""
    model_config = ConfigDict(extra='forbid', strict=True)
    window_id: Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9_:\-]{1,100}$')]
    machine_ids: Annotated[list[MachineId], Field(min_length=32, max_length=200)]

    @model_validator(mode='after')
    def unique_cohort(self):
        """Reject repeated machine IDs instead of silently overstating the comparison sample."""
        if len(set(self.machine_ids)) != len(self.machine_ids): raise ValueError('Cohort machine IDs must be unique')
        return self


class MachineData(BaseModel):
    """Require the native feature schema and both simulated and preparation-time provenance."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    machine_id: MachineId
    features: FeatureVector
    feature_event_ts: AwareDatetime
    feature_age_seconds: Annotated[float, Field(ge=0, le=600)]
    operating_context: dict
    as_of: AwareDatetime
    clock_mode: Literal['simulation']
    prepared_wall_utc: AwareDatetime
    protocol_sha256: Digest
    source_manifest_sha256: Digest

    @model_validator(mode='after')
    def valid_simulated_times(self):
        """Do not accept a future feature or a clock-age field inconsistent with its source timestamps."""
        age = (self.as_of - self.feature_event_ts).total_seconds()
        if not 0 <= age <= 600 or abs(age-self.feature_age_seconds) > .001:
            raise ValueError('Feature age differs from the actual simulated timestamps')
        if self.operating_context.get('status') not in ['operating', 'failed', 'maintenance']:
            raise ValueError('Unknown operating state')
        for field in ['event_ts', 'received_ts']:
            timestamp = self.operating_context.get(field)
            if not isinstance(timestamp, str): raise ValueError('Operating timestamps must be ISO8601 strings')
            value = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            if value.tzinfo is None or value > self.as_of: raise ValueError('Unavailable operating context')
        event = datetime.fromisoformat(self.operating_context['event_ts'].replace('Z','+00:00'))
        received = datetime.fromisoformat(self.operating_context['received_ts'].replace('Z','+00:00'))
        if event > received or (self.as_of-event).total_seconds() > 600: raise ValueError('Operating context is late or stale')
        return self


class PredictionData(BaseModel):
    """Preserve the native prediction's feature trace and validate its decision against the returned threshold."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    prediction: dict
    model: dict
    feature_trace: MachineData

    @model_validator(mode='after')
    def valid_decision(self):
        """Reject malformed scores, invalid decisions and model/feature protocol disagreement."""
        score = self.prediction.get('risk_score'); decision = self.prediction.get('predicted_failure')
        threshold = self.model.get('threshold')
        if isinstance(score,bool) or not isinstance(score,(int,float)) or not 0 <= score <= 1:
            raise ValueError('Invalid risk score')
        if isinstance(threshold,bool) or not isinstance(threshold,(int,float)) or not 0 <= threshold <= 1:
            raise ValueError('Invalid model threshold')
        if type(decision) is not int or decision != int(score >= threshold): raise ValueError('Invalid prediction decision')
        if self.model.get('production') is not True or self.model.get('protocol_sha256') != self.feature_trace.protocol_sha256:
            raise ValueError('Native model/feature binding differs')
        if self.feature_trace.operating_context['status'] != 'operating': raise ValueError('Prediction is undefined for stopped machines')
        return self


class DriftScore(BaseModel):
    """Validate a native finite PSI score and its histogram evidence before summarizing it for an agent."""
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    psi: Annotated[float, Field(ge=0)]
    drifted: bool
    cuts: list[float]
    reference_counts: list[int]
    current_counts: list[int]


class DriftAssessment(BaseModel):
    """Require all eight native drift results and explicit label-free interpretation limits."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    reference_id: Digest
    window_id: str
    start: AwareDatetime
    end: AwareDatetime
    unique_rows: Annotated[int, Field(ge=32,le=200)]
    reference_rows: Annotated[int, Field(ge=32,le=2048)]
    lineage: dict
    policy: dict
    features: dict[str,DriftScore]
    drifted_feature_fraction: Annotated[float, Field(ge=0,le=1)]
    drift_detected: bool
    scope: Literal['Label-free marginal covariate drift; no ground truth, accuracy or concept-drift claim.']

    @model_validator(mode='after')
    def complete_scores(self):
        """Do not accept missing features, inconsistent alert flags or an unexpected detection policy."""
        if set(self.features) != set(FeatureVector.model_fields): raise ValueError('Drift feature schema differs')
        if self.policy != {'bins':10,'psi_threshold':0.2,'drifted_feature_fraction':0.25,'pseudocount':0.5}:
            raise ValueError('Drift policy differs')
        if any(score.drifted != (score.psi >= .2) for score in self.features.values()): raise ValueError('PSI decision differs')
        fraction = sum(score.drifted for score in self.features.values())/len(self.features)
        if fraction != self.drifted_feature_fraction or self.drift_detected != (fraction >= .25):
            raise ValueError('Aggregate drift decision differs')
        return self
