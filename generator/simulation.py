"""Simulate pump wear, load, repair and preventive maintenance before adding transport faults."""

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json
import math
import random

from generator.config import Config, iso_ts


@dataclass
class Dataset:
    """Keep the small source tables and stream replay fixture in memory."""

    machines: list[dict]
    maintenance: list[dict]
    failures: list[dict]
    telemetry: list[dict]
    stream: list[dict]


def rng_for(config: Config, purpose: str) -> random.Random:
    """Isolate random streams so delivery settings cannot change physical failures."""
    digest = sha256(f"{config.seed}:{purpose}".encode()).digest()
    return random.Random(int.from_bytes(digest, "big"))


def physical_identity(config: Config) -> str:
    """Keep physical IDs independent of transport faults and storage chunk boundaries."""
    delivery_options = {'offline_duplicate_rate', 'stream_duplicate_rate', 'late_rate',
                        'late_min_seconds', 'late_max_seconds', 'retry_delay_seconds', 'export_delay_seconds'}
    physical_config={key:value for key,value in config.to_dict().items() if key not in delivery_options}
    return sha256(json.dumps(physical_config,sort_keys=True).encode()).hexdigest()[:16]


def machine_records(config: Config, machine_number: int):
    """Yield one pump's metadata, stop events and readings without resetting wear across chunks."""
    hot_count=math.floor(config.machine_count*config.hot_factory_share+0.5)
    v2_step=math.floor(config.steps*config.schema_v2_fraction)
    run_id=physical_identity(config)
    machine_id = f'P-{machine_number + 1:03d}'
    rng = rng_for(config, machine_id)
    factory_id = 'F-01' if machine_number < hot_count else f'F-{2 + (machine_number - hot_count) % 2:02d}'
    yield 'machines', {'machine_id': machine_id, 'factory_id': factory_id,
                       'machine_type': 'centrifugal_pump', 'rated_rpm': 3000,
                       'installed_at': iso_ts(config.start - timedelta(days=180 + machine_number * 7)),
                       'valid_from_ts': iso_ts(config.start), 'valid_to_ts': None, 'is_current': True}
    wear = rng.uniform(0.15, 0.85)
    susceptibility = rng.uniform(0.75, 1.5)
    phase = rng.uniform(0, math.tau)
    stop_until = config.start
    stop_reason = 'operating'
    period = config.maintenance_every_hours * 3600 // config.sample_seconds
    first_maintenance = period + machine_number * 300 // config.sample_seconds
    for step in range(config.steps):
        now = config.start + timedelta(seconds=step * config.sample_seconds)
        if now >= stop_until:
            stop_reason = 'operating'
            if step >= first_maintenance and (step - first_maintenance) % period == 0:
                stop_reason = 'maintenance'
                stop_until = now + timedelta(minutes=config.maintenance_minutes)
                yield 'maintenance', {'maintenance_id': f'M-{run_id}-{machine_id}-{step:06d}',
                                      'machine_id': machine_id, 'start_ts': iso_ts(now),
                                      'end_ts': iso_ts(stop_until) if stop_until < config.end else None,
                                      'maintenance_type': 'preventive', 'component': 'bearing', 'created_ts': iso_ts(now)}
                wear *= 0.35
        load = 0.0
        if stop_reason == 'operating':
            load = min(1.2, max(0.25, 0.7 + 0.25 * math.sin(step / (3600 / config.sample_seconds) + phase)
                                + rng.gauss(0, 0.1)))
            wear += max(0, config.wear_per_hour * config.sample_seconds / 3600
                        * susceptibility * (0.6 + load) + rng.gauss(0, 0.0004))
            if wear >= config.failure_threshold:
                stop_reason = 'failed'
                stop_until = now + timedelta(minutes=config.repair_minutes)
                yield 'failures', {'failure_id': f'F-{run_id}-{machine_id}-{step:06d}',
                                   'machine_id': machine_id, 'failure_start_ts': iso_ts(now),
                                   'recovered_ts': iso_ts(stop_until) if stop_until < config.end else None,
                                   'failure_type': 'bearing_wear', 'recorded_ts': iso_ts(now + timedelta(seconds=30))}
                load = 0.0
                wear = rng.uniform(0.05, 0.2)
        operating = stop_reason == 'operating'
        # Latent wear drives readings/failures but is never exported as a prediction feature.
        temperature = 34 + 27 * load + (17 * wear if operating else 0) + rng.gauss(0, 2.0)
        vibration = max(0, 0.5 + 1.5 * load + (6 * wear if operating else 0) + rng.gauss(0, 0.4))
        row = {'event_id': f'E-{run_id}-{machine_id}-{step:06d}', 'machine_id': machine_id,
               'event_ts': iso_ts(now), 'received_ts': iso_ts(now), 'created_ts': iso_ts(now),
               'temperature_c': round(temperature, 3), 'vibration_rms_mm_s': round(vibration, 3),
               'current_a': round(max(0, 4 + 16 * load + 2 * wear + rng.gauss(0, 0.6)), 3) if operating else 0.0,
               'rpm': round(max(0, 3000 * (0.85 + 0.15 * load) + rng.gauss(0, 20)), 1) if operating else 0.0,
               'operating_status': stop_reason, 'schema_version': 2 if step >= v2_step else 1}
        if row['schema_version'] == 2:
            row['pressure_bar'] = round(max(0, 2 + 3 * load - 0.4 * wear + rng.gauss(0, 0.12)), 3) if operating else 0.0
        yield 'telemetry', row


def simulate(config: Config) -> Dataset:
    """Collect the unchanged physical process for the capped legacy in-memory command."""
    tables={name:[] for name in ('machines','maintenance','failures','telemetry')}
    for machine_number in range(config.machine_count):
        for kind,row in machine_records(config,machine_number):
            tables[kind].append(row)
    tables['telemetry'].sort(key=lambda row:(row['event_ts'],row['machine_id']))
    return Dataset(tables['machines'],tables['maintenance'],tables['failures'],tables['telemetry'],[])

def add_delivery_faults(dataset: Dataset, config: Config) -> Dataset:
    """Add exact sampled late events and independent offline/stream duplicates to clean truth."""
    count = len(dataset.telemetry)
    late_rng = rng_for(config, "late")
    late_indices = set(late_rng.sample(range(count), math.floor(count * config.late_rate)))
    offline_indices = set(rng_for(config, "offline").sample(range(count), math.floor(count * config.offline_duplicate_rate)))
    stream_indices = set(rng_for(config, "stream").sample(range(count), math.floor(count * config.stream_duplicate_rate)))
    telemetry, stream = [], []
    for index, original in enumerate(dataset.telemetry):
        row = original.copy()
        delay = late_rng.randint(config.late_min_seconds, config.late_max_seconds) if index in late_indices else late_rng.randint(0, 3)
        received = config.start + timedelta(seconds=(index // config.machine_count) * config.sample_seconds + delay)
        row["received_ts"] = row["created_ts"] = iso_ts(received)
        telemetry.append(row)
        if index in offline_indices:
            telemetry.append({**row, "created_ts": iso_ts(received + timedelta(seconds=config.export_delay_seconds))})
        stream.append({"delivery_id": f"{row['event_id']}-1", "attempt": 1,
                       "published_ts": iso_ts(received), "event": row.copy()})
        if index in stream_indices:
            stream.append({"delivery_id": f"{row['event_id']}-2", "attempt": 2,
                           "published_ts": iso_ts(received + timedelta(seconds=config.retry_delay_seconds)), "event": row.copy()})
    stream.sort(key=lambda delivery: (delivery["published_ts"], delivery["delivery_id"]))
    return Dataset(dataset.machines, dataset.maintenance, dataset.failures, telemetry, stream)


def generate(config: Config) -> Dataset:
    """Run the physical simulator followed by the independent delivery simulator."""
    return add_delivery_faults(simulate(config), config)
