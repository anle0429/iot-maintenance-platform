"""Gate a real 100GB source run using measured intermediate correctness, per-volume reserves and session time."""

import math


def scale_plan(measured: dict, ingest: dict, available: dict, unique_rows: int,
               remaining_seconds: float, safety_factor: float = 1.2) -> dict:
    """Keep forecasts explicitly separate from actual source size and require matching validated Spark input."""
    if measured.get('passed') is not True or ingest.get('passed') is not True:
        raise ValueError('intermediate generation/validation and Spark must pass first')
    actual = measured['actual_offline_payload_bytes']
    if actual < 1_000_000_000 or measured['source_manifest_sha256'] != ingest['source_manifest_sha256']:
        raise ValueError('intermediate must exceed 1GB and Spark must read the same source')
    if actual != ingest['source_offline_bytes'] or measured['unique_rows'] != ingest['silver_unique_rows']:
        raise ValueError('intermediate Spark byte/key counts differ')
    if unique_rows <= 0 or not math.isfinite(safety_factor) or safety_factor < 1.1:
        raise ValueError('invalid target rows or timing safety factor')
    estimated_bytes = math.ceil(unique_rows * actual / measured['unique_rows'])
    if not 100_000_000_000 <= estimated_bytes <= 110_000_000_000:
        raise ValueError('target shape is outside the reviewed 100-110GB source envelope')
    phases = measured['phase_reports']
    if not all(phases[name].get('passed') is True and phases[name]['seconds'] > 0 for name in ('generate', 'validate')):
        raise ValueError('both measured timing phases are required')
    estimated_seconds = (phases['generate']['seconds'] + phases['validate']['seconds']) * estimated_bytes / actual * safety_factor
    hdd_budget = {'source_hard_cap': 115_000_000_000, 'future_processed_outputs': 150_000_000_000,
                  'future_shuffle_on_hdd': 100_000_000_000, 'logs': 5_000_000_000, 'free_reserve': 30_000_000_000}
    ssd_budget = {'platform_growth': 15_000_000_000, 'free_reserve': 20_000_000_000}
    gates = {'hdd': available['free_bytes']['/srv/iot-data'] >= sum(hdd_budget.values()),
             'ssd': available['free_bytes']['/srv/iot-fast'] >= sum(ssd_budget.values()),
             'ram': available['memory_available_bytes'] >= 36 * 1024**3,
             'session_time': remaining_seconds >= estimated_seconds + 1200}
    return {'kind': 'estimate_only_until_generation_completes', 'generation_eligible': all(gates.values()),
            'gates': gates, 'target_offline_payload_bytes': 100_000_000_000,
            'estimated_offline_payload_bytes': estimated_bytes, 'target_unique_rows': unique_rows,
            'estimated_generate_and_validate_seconds': estimated_seconds, 'timing_safety_factor': safety_factor,
            'session_finish_reserve_seconds': 1200, 'session_remaining_seconds': remaining_seconds,
            'hdd_budget_bytes': hdd_budget, 'ssd_budget_bytes': ssd_budget, 'actual_resources': available,
            'source_reserve_bytes': sum(hdd_budget.values()) - hdd_budget['source_hard_cap'],
            'intermediate_manifest_sha256': measured['source_manifest_sha256'],
            'large_spark_execution_authorized': False,
            'scope': 'Only source generation/readback is gated here. Future 100GB Spark scratch is reserved on HDD; the existing optimized launcher still uses SSD and must not inherit this budget automatically.'}
