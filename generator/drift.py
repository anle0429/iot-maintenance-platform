"""Add explicit sensor calibration drift to a small paired simulation without changing physical truth."""
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
import argparse
import json
import math

from generator.config import Config, parse_ts
from generator.simulation import generate, Dataset
from generator.storage import write_dataset, write_json


@dataclass(frozen=True)
class SensorDrift:
    """Describe a step/ramp in sensor calibration, separate from wear and failure generation."""
    start_ts: str
    ramp_seconds: int = 0
    temperature_offset_c: float = 0.0
    vibration_multiplier: float = 1.0

    def __post_init__(self):
        """Reject ambiguous time and invalid transformations before generating any files."""
        parse_ts(self.start_ts)
        if type(self.ramp_seconds) is not int or self.ramp_seconds < 0:
            raise ValueError('ramp_seconds must be a nonnegative integer')
        for name in ('temperature_offset_c', 'vibration_multiplier'):
            value = getattr(self, name)
            if type(value) not in (float, int) or not math.isfinite(value):
                raise ValueError('Sensor adjustments must be finite numbers')
        if not -100 <= self.temperature_offset_c <= 100 or not 0.1 <= self.vibration_multiplier <= 10:
            raise ValueError('Sensor adjustments exceed the documented simulation range')

    def transform(self, row):
        """Apply the same event-time transformation to an original and every delivery retry."""
        elapsed = (parse_ts(row['event_ts']) - parse_ts(self.start_ts)).total_seconds()
        fraction = 0.0 if elapsed < 0 else (min(1.0, elapsed / self.ramp_seconds) if self.ramp_seconds else 1.0)
        return {**row,
                'temperature_c': round(row['temperature_c'] + self.temperature_offset_c * fraction, 3),
                'vibration_rms_mm_s': round(row['vibration_rms_mm_s'] * (1 + (self.vibration_multiplier - 1) * fraction), 3)}


def generate_drift(config: Config, profile: SensorDrift) -> Dataset:
    """Preserve failures/stops/arrival times; namespace telemetry IDs so scenarios cannot silently collide."""
    if not config.start <= parse_ts(profile.start_ts) < config.end:
        raise ValueError('Drift start must be inside the observation interval')
    base = generate(config)
    scenario = sha256(json.dumps(asdict(profile), sort_keys=True).encode()).hexdigest()[:12]
    def transform(row):
        """Keep duplicates byte-consistent apart from their pre-existing export timestamps."""
        return {**profile.transform(row), 'event_id': f"D-{scenario}-{row['event_id']}"}
    telemetry = [transform(row) for row in base.telemetry]
    stream = [{**delivery, 'delivery_id': f"D-{scenario}-{delivery['delivery_id']}",
               'event': transform(delivery['event'])} for delivery in base.stream]
    return Dataset(base.machines, base.maintenance, base.failures, telemetry, stream)


def main():
    """Write a new immutable small source plus its explicit sensor-drift configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    settings = json.loads(args.config.read_text(encoding='utf-8'))
    if set(settings) != {'simulation', 'sensor_drift'}:
        raise ValueError('Configuration must contain only simulation and sensor_drift')
    config, profile = Config(**settings['simulation']), SensorDrift(**settings['sensor_drift'])
    manifest = write_dataset(generate_drift(config, profile), config, args.output)
    manifest['sensor_drift'] = asdict(profile)
    manifest['scope'] = 'Small sensor-calibration drift simulation; physical wear/failures unchanged. Not the historical100GB source or chunked drift.'
    write_json(args.output / 'manifest.json', manifest)
    print(json.dumps({'unique_rows': config.unique_rows, 'files': len(manifest['files']), 'sensor_drift': asdict(profile)}))


if __name__ == '__main__':
    main()
