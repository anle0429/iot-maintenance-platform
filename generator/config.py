"""Load and validate the reproducible simulation and delivery configuration."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from typing import ClassVar


def parse_ts(value: str) -> datetime:
    """Parse an aware timestamp and normalize it to UTC."""
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return timestamp.astimezone(timezone.utc)


def iso_ts(value: datetime) -> str:
    """Serialize UTC timestamps with a stable seconds-level representation."""
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Config:
    """Describe a small simulation; rates count extra/selected rows per unique event."""

    row_limit: ClassVar[int | None] = 100_000

    seed: int = 42
    start_ts: str = "2026-10-01T00:00:00Z"
    duration_hours: int = 48
    machine_count: int = 20
    sample_seconds: int = 60
    hot_factory_share: float = 0.70
    schema_v2_fraction: float = 0.50
    offline_duplicate_rate: float = 0.02
    stream_duplicate_rate: float = 0.015
    late_rate: float = 0.05
    late_min_seconds: int = 300
    late_max_seconds: int = 600
    retry_delay_seconds: int = 2
    export_delay_seconds: int = 3600
    wear_per_hour: float = 0.035
    failure_threshold: float = 1.0
    repair_minutes: int = 45
    maintenance_every_hours: int = 18
    maintenance_minutes: int = 20

    def __post_init__(self) -> None:
        """Reject invalid ranges, ambiguous time grids and unsafe in-memory workloads."""
        for name in ("seed", "duration_hours", "machine_count", "sample_seconds",
                     "late_min_seconds", "late_max_seconds", "retry_delay_seconds",
                     "export_delay_seconds", "repair_minutes", "maintenance_every_hours",
                     "maintenance_minutes"):
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == "seed" else 1):
                raise ValueError(f"{name} must be an integer in its valid positive range")
        for name in ("hot_factory_share", "schema_v2_fraction", "offline_duplicate_rate",
                     "stream_duplicate_rate", "late_rate"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        for name in ("wear_per_hour", "failure_threshold"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        parse_ts(self.start_ts)
        if self.duration_hours * 3600 % self.sample_seconds:
            raise ValueError("duration must be divisible by sample_seconds")
        if self.sample_seconds > 60 or 60 % self.sample_seconds:
            raise ValueError("sample_seconds must divide 60, with a maximum of 60")
        if self.late_min_seconds > self.late_max_seconds or self.late_min_seconds <= 3:
            raise ValueError("late delays must be ordered and greater than the normal 0..3s delay")
        if self.row_limit is not None and self.unique_rows > self.row_limit:
            raise ValueError("day-1 in-memory generator is capped at 100,000 unique rows; use generator.chunked for scale")

    @property
    def start(self) -> datetime:
        """Return the observation start in UTC."""
        return parse_ts(self.start_ts)

    @property
    def end(self) -> datetime:
        """Return the exclusive end of the observed simulation interval."""
        return self.start + timedelta(hours=self.duration_hours)

    @property
    def steps(self) -> int:
        """Return the number of readings per machine, including stopped machines."""
        return self.duration_hours * 3600 // self.sample_seconds

    @property
    def unique_rows(self) -> int:
        """Return the expected number of unique telemetry events."""
        return self.steps * self.machine_count

    def to_dict(self) -> dict:
        """Return JSON-serializable configuration values."""
        return asdict(self)

    @classmethod
    def load(cls, path: Path) -> "Config":
        """Read a JSON configuration, rejecting misspelled or unknown options."""
        return cls(**json.loads(path.read_text(encoding="utf-8")))
