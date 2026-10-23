"""Estimate a future 100 GB disk budget from measured pilot bytes without claiming that volume exists."""

import math
from pathlib import Path
import shutil

from generator.chunked import existing_parent


def capacity_plan(quality: dict, source: Path, target_offline_gb: float = 100, offline_only: bool = False) -> dict:
    """Budget source, outputs, shuffle, logs and reserve conservatively from a validated pilot."""
    if not math.isfinite(target_offline_gb) or target_offline_gb<=0:
        raise ValueError('target offline GB must be positive and finite')
    target=math.ceil(target_offline_gb*1_000_000_000)
    bytes_per_unique=quality['offline_payload_bytes']/quality['unique_telemetry_rows']
    source_factor=1 if offline_only else quality['source_total_bytes']/quality['offline_payload_bytes']
    if not offline_only and not quality['stream']['generated']:
        raise ValueError('pilot lacks stream bytes; choose offline-only instead of inventing a stream estimate')
    # These are explicit assumptions, not measurements of future Spark workloads.
    budget={'source_with_20_percent_margin':math.ceil(target*source_factor*1.2),
            'processed_outputs_1_5_times_offline':math.ceil(target*1.5),
            'shuffle_1_times_offline':target,'logs':5_000_000_000,'free_reserve':20_000_000_000}
    needed=sum(budget.values())
    free=shutil.disk_usage(existing_parent(source)).free
    return {'passed':True,'kind':'estimate_only','target_offline_bytes':target,
            'measured_pilot_offline_bytes':quality['offline_payload_bytes'],
            'measured_pilot_unique_rows':quality['unique_telemetry_rows'],
            'measured_bytes_per_unique_including_offline_extras_and_metadata':bytes_per_unique,
            'estimated_unique_rows_for_target':math.ceil(target/bytes_per_unique),
            'future_stream_enabled':not offline_only,'assumed_budget_bytes':budget,
            'estimated_required_free_bytes':needed,'actual_free_bytes_now':free,
            'disk_budget_fits_now':free>=needed,'generation_authorized_by_plan':False,
            'scope':'No generation performed. Physical row widths/event frequency can change with horizon; measure an intermediate continuous workload and ingest/shuffle footprint before a large run.'}
