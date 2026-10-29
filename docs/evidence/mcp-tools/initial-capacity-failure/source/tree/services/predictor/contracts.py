"""Validate exactly the eight training features; never accept labels or silently coerce strings."""

from typing import Annotated
from pydantic import BaseModel,ConfigDict,Field


class FeatureVector(BaseModel):
    """One operating-machine snapshot; large numeric caps protect arithmetic, not calibration."""

    model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)
    temperature_mean_10m:Annotated[float,Field(ge=-273.15,le=1e6)]
    vibration_max_10m:Annotated[float,Field(ge=0,le=1e6)]
    current_mean_10m:Annotated[float,Field(ge=0,le=1e6)]
    current_std_10m:Annotated[float,Field(ge=0,le=1e6)]
    rpm_mean_10m:Annotated[float,Field(ge=0,le=1e9)]
    reading_count_10m:Annotated[int,Field(ge=1,le=1_000_000)]
    machine_age_days:Annotated[float,Field(ge=0,le=1e6)]
    prior_failures_24h:Annotated[int,Field(ge=0,le=1_000_000)]


class PredictRequest(BaseModel):
    """KServe V1 instances with a fixed maximum batch size."""

    model_config=ConfigDict(extra='forbid',strict=True)
    instances:Annotated[list[FeatureVector],Field(min_length=1,max_length=128)]
