"""Validate bounded, arrival-aware drift windows and canonicalize duplicate observations."""
from datetime import datetime, timezone
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator
from services.predictor.contracts import FeatureVector


class Observation(BaseModel):
    """One feature snapshot available before the window closes; no label is accepted."""
    model_config = ConfigDict(extra='forbid')
    id: Annotated[str, Field(pattern=r'^[A-Za-z0-9_:\-]{1,100}$')]
    event_timestamp: AwareDatetime
    available_at: AwareDatetime
    features: FeatureVector


class CompareRequest(BaseModel):
    """An immutable window with at least32 unique snapshots and a named reference artifact."""
    model_config = ConfigDict(extra='forbid')
    reference_id: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    window_id: Annotated[str, Field(pattern=r'^[A-Za-z0-9_:\-]{1,100}$')]
    start: AwareDatetime
    end: AwareDatetime
    observations: Annotated[list[Observation], Field(min_length=32, max_length=2048)]

    @model_validator(mode='after')
    def validate_window(self):
        """Reject future/unavailable rows, conflicting IDs and duplicate-only undersized samples."""
        if not self.start < self.end:
            raise ValueError('Window must have start < end')
        unique = {}
        for row in self.observations:
            if not self.start <= row.event_timestamp < self.end or not row.event_timestamp <= row.available_at <= self.end:
                raise ValueError('Rows must be inside the window and available by its end')
            value = row.model_dump(mode='json')
            if row.id in unique and unique[row.id] != value:
                raise ValueError('Conflicting duplicate observation ID')
            unique[row.id] = value
        if len(unique) < 32:
            raise ValueError('Require at least32 unique observations')
        return self

    def canonical(self):
        """Normalize UTC and row order so exact retries cannot change the cached assessment."""
        utc = lambda value: value.astimezone(timezone.utc).isoformat()
        rows = {row.id: {'id': row.id, 'event_timestamp': utc(row.event_timestamp),
                        'available_at': utc(row.available_at), 'features': row.features.model_dump()}
                for row in self.observations}
        return {'reference_id': self.reference_id, 'window_id': self.window_id,
                'start': utc(self.start), 'end': utc(self.end),
                'observations': [rows[key] for key in sorted(rows)]}
