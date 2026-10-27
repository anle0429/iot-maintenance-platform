"""Forecast the existing full ingest workload from bound intermediate measurements and a read-only disk probe."""

import math


def large_ingest_plan(source: dict, intermediate: dict, ingest: dict, metrics: dict,
                      probe: dict, resources: dict, remaining_seconds: float) -> dict:
    """Separate conservative estimates from execution evidence; this planner never authorizes or launches Spark."""
    if any(item.get('passed') is not True for item in (source, intermediate, ingest, probe)):
        raise ValueError('source, intermediate, ingest and disk probe must have passed')
    if (intermediate['source_manifest_sha256'] != ingest['source_manifest_sha256'] or
            intermediate['actual_offline_payload_bytes'] != ingest['source_offline_bytes'] or
            intermediate['unique_rows'] != ingest['silver_unique_rows']):
        raise ValueError('intermediate correctness measurements belong to different inputs')
    if source['source_manifest_sha256'] != probe['source_manifest_sha256']:
        raise ValueError('disk probe is not bound to the large source')
    if metrics['failed_attempts'] != 0 or metrics['task_attempts'] < 1:
        raise ValueError('intermediate task metrics contain a failure or no completed work')
    numeric = [remaining_seconds, source['actual_offline_payload_bytes'],
               intermediate['actual_offline_payload_bytes'], ingest['job_wall_seconds'],
               probe['bytes_per_second'], probe['actual_read_bytes']]
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n <= 0 for n in numeric):
        raise ValueError('positive finite measurements are required')
    if source['actual_offline_payload_bytes'] < 100_000_000_000 or intermediate['actual_offline_payload_bytes'] < 1_000_000_000:
        raise ValueError('large source or intermediate is below its declared scale')
    io_keys = ('input_bytes_read', 'output_bytes_written', 'shuffle_bytes_read', 'shuffle_bytes_written', 'disk_bytes_spilled')
    if any(type(metrics[k]) is not int or metrics[k] < 0 for k in io_keys):
        raise ValueError('task IO counters must be nonnegative integer bytes')
    ratio = source['actual_offline_payload_bytes'] / intermediate['actual_offline_payload_bytes']
    # Existing run-cloud-ingest does one host scan; ChunkSource does pre/post Spark scans.
    # Retain all three until a separately tested launcher removes the redundant host scan.
    checksum_passes = 3
    scaled_task_io = math.ceil(ratio * (sum(metrics[k] for k in io_keys[:-1]) + 2 * metrics['disk_bytes_spilled']))
    checksum_io = source['actual_offline_payload_bytes'] * checksum_passes
    estimated_io = scaled_task_io + checksum_io
    linear_seconds = ingest['job_wall_seconds'] * ratio
    disk_seconds = estimated_io / probe['bytes_per_second']
    factor, finish_reserve = 1.3, 1200
    estimated_seconds = max(linear_seconds, disk_seconds) * factor
    # Source already exists: reserving it again would double-count its occupied space.
    hdd = {'processed_outputs': 150_000_000_000, 'scratch': 120_000_000_000,
           'logs': 5_000_000_000, 'free_reserve': 30_000_000_000}
    ssd = {'platform_growth': 15_000_000_000, 'free_reserve': 20_000_000_000}
    gates = {'hdd': resources['free_bytes']['/srv/iot-data'] >= sum(hdd.values()),
             'ssd': resources['free_bytes']['/srv/iot-fast'] >= sum(ssd.values()),
             'ram': resources['memory_available_bytes'] >= 36 * 1024**3,
             'cpu': resources['cpu_count'] >= 7,
             'session_time': remaining_seconds >= estimated_seconds + finish_reserve}
    return {'kind': 'forecast_only_not_execution_evidence', 'resource_time_gates_pass': all(gates.values()),
            'gates': gates, 'source_manifest_sha256': source['source_manifest_sha256'],
            'actual_source_offline_bytes': source['actual_offline_payload_bytes'],
            'intermediate_manifest_sha256': intermediate['source_manifest_sha256'],
            'intermediate_to_large_ratio': ratio, 'checksum_passes_in_current_launcher': checksum_passes,
            'estimated_task_io_bytes': scaled_task_io, 'estimated_checksum_io_bytes': checksum_io,
            'estimated_total_io_bytes': estimated_io, 'measured_probe_bytes_per_second': probe['bytes_per_second'],
            'linear_spark_seconds': linear_seconds, 'io_model_seconds': disk_seconds,
            'safety_factor': factor, 'estimated_job_seconds': estimated_seconds,
            'finish_reserve_seconds': finish_reserve, 'remaining_session_seconds': remaining_seconds,
            'hdd_growth_and_reserve_bytes': hdd, 'ssd_growth_and_reserve_bytes': ssd,
            'minimum_available_ram_bytes': 36 * 1024**3, 'scratch_volume': '/srv/iot-data',
            'execution_ready': False,
            'blocking_implementation': ['existing optimized launcher still selects SSD scratch',
                                        'large runner needs live time/disk/RAM guards and bounded partition review'],
            'scope': 'Forecast existing Bronze/Silver/checks/three query pairs only; no Gold, DP3, ML, '
                     '100GB Spark success, disk throughput guarantee or VM-session extension. O_DIRECT sample '
                     'does not emulate mixed concurrent shuffle IO; RAM is a resource budget, not a scale prediction.'}
