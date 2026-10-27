"""Benchmark an identical factory-hour alarm query with sort-merge and broadcast joins on immutable Silver."""

import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import platform
import statistics
import time

from pyspark.sql import SparkSession, functions as F, types as T

from pipelines.factory_policy import load_policy
from pipelines.orchestration_contract import save, utc_now


def policy_frame(spark, policy: dict):
    """Create an explicitly bounded three-column dimension, rather than assuming its statistics are present."""
    schema = T.StructType([T.StructField('factory_id', T.StringType(), False),
                           T.StructField('temperature_limit_milli', T.LongType(), False),
                           T.StructField('vibration_limit_milli', T.LongType(), False)])
    return spark.createDataFrame([tuple(row[key] for key in schema.fieldNames()) for row in policy['factories']], schema)


def prepare_readings(frame):
    """Project the same columns for both strategies and express three-decimal sensors as exact integers."""
    return frame.select('factory_id', 'event_ts', 'operating_status',
                        (F.col('temperature_c').cast('decimal(12,3)') * 1000).cast('long').alias('temperature_milli'),
                        (F.col('vibration_rms_mm_s').cast('decimal(12,3)') * 1000).cast('long').alias('vibration_milli'))


def enrich_factory(readings, machines):
    """Resolve the normalized Silver machine key through one bounded, unique machine snapshot for both strategies."""
    if 'factory_id' in readings.columns:
        raise ValueError('expected normalized Silver without a conflicting factory attribute')
    count = machines.limit(10_001).count()
    if not 1 <= count <= 10_000:
        raise ValueError('machine snapshot exceeds the shared broadcast bound')
    if machines.filter(F.col('machine_id').isNull() | F.col('factory_id').isNull()).limit(1).count():
        raise ValueError('machine snapshot contains missing keys')
    if machines.groupBy('machine_id').count().filter('count!=1').limit(1).count():
        raise ValueError('machine snapshot key is not unique')
    # A left join exposes unmatched keys to the later factory guard instead of dropping readings silently.
    return readings.join(F.broadcast(machines.select('machine_id', 'factory_id')), 'machine_id', 'left')


def alarm_query(readings, policies, strategy: str):
    """Join on the naturally skewed factory key, then finalize hourly operating alarms and exact sensor sums."""
    if strategy == 'baseline':
        joined = readings.hint('merge').join(policies.hint('merge'), 'factory_id')
    elif strategy == 'broadcast':
        joined = readings.join(F.broadcast(policies), 'factory_id')
    else:
        raise ValueError('unknown join strategy')
    operating = F.col('operating_status') == 'operating'
    alarm = operating & ((F.col('temperature_milli') > F.col('temperature_limit_milli')) |
                         (F.col('vibration_milli') > F.col('vibration_limit_milli')))
    return (joined.groupBy('factory_id', F.date_trunc('hour', 'event_ts').alias('event_hour'))
            .agg(F.count('*').alias('reading_count'), F.sum(operating.cast('long')).alias('operating_count'),
                 F.sum(alarm.cast('long')).alias('alarm_count'),
                 F.sum('temperature_milli').alias('temperature_milli_sum'),
                 F.max('vibration_milli').alias('vibration_milli_max')))


def canonical_rows(frame) -> list[dict]:
    """Collect only bounded factory-hour aggregates; timestamps and integer sums have a stable comparison encoding."""
    rows = frame.limit(100_001).collect()
    if len(rows) > 100_000:
        raise ValueError('aggregate result exceeds the benchmark driver bound')
    result = []
    for row in rows:
        value = row.asDict()
        value['event_hour'] = value['event_hour'].isoformat() + 'Z'
        result.append(value)
    return sorted(result, key=lambda row: (row['factory_id'], row['event_hour']))


def clear_group(context) -> None:
    """Clear the documented job-group local properties without relying on a version-specific Python helper."""
    for name in ('spark.jobGroup.id', 'spark.job.description', 'spark.job.interruptOnCancel'):
        context.setLocalProperty(name, None)


def known_answer_checks(spark) -> list[str]:
    """Check strict thresholds, stopped machines and the hour boundary against hand-computed results."""
    data = [('F-01', datetime(2026, 10, 1, 1), 'operating', 75000, 7000),
            ('F-01', datetime(2026, 10, 1, 1, 59), 'operating', 76000, 6000),
            ('F-01', datetime(2026, 10, 1, 2), 'failed', 100000, 9000),
            ('F-02', datetime(2026, 10, 1, 2), 'operating', 30000, 7501)]
    frame = spark.createDataFrame(data, 'factory_id string,event_ts timestamp,operating_status string,temperature_milli long,vibration_milli long')
    expected = [dict(factory_id='F-01', event_hour='2026-10-01T01:00:00Z', reading_count=2, operating_count=2,
                     alarm_count=1, temperature_milli_sum=151000, vibration_milli_max=7000),
                dict(factory_id='F-01', event_hour='2026-10-01T02:00:00Z', reading_count=1, operating_count=0,
                     alarm_count=0, temperature_milli_sum=100000, vibration_milli_max=9000),
                dict(factory_id='F-02', event_hour='2026-10-01T02:00:00Z', reading_count=1, operating_count=1,
                     alarm_count=1, temperature_milli_sum=30000, vibration_milli_max=7501)]
    small_policy = {'version': 'fixture', 'factories': [dict(factory_id='F-01', temperature_limit_milli=75000, vibration_limit_milli=7000),
                                                       dict(factory_id='F-02', temperature_limit_milli=80000, vibration_limit_milli=7500)]}
    for strategy in ('baseline', 'broadcast'):
        actual = canonical_rows(alarm_query(frame, policy_frame(spark, small_policy), strategy))
        if actual != expected:
            raise ValueError(f'known-answer {strategy} differs: {actual}')
    normalized = spark.createDataFrame([('P-001', 1), ('P-002', 2), ('P-003', 3)], 'machine_id string,value long')
    machines = spark.createDataFrame([('P-001', 'F-01'), ('P-002', 'F-02')], 'machine_id string,factory_id string')
    enriched = enrich_factory(normalized, machines)
    if enriched.count() != 3 or enriched.filter('factory_id IS NULL').count() != 1:
        raise ValueError('machine enrichment hid an unmatched key or multiplied readings')
    try:
        enrich_factory(normalized, machines.unionByName(machines))
    except ValueError as error:
        if 'not unique' not in str(error):
            raise
    else:
        raise ValueError('ambiguous machine dimension was accepted')
    return ['strict_threshold_boundary', 'stopped_machine_excluded_from_alarm', 'hour_boundary',
            'different_factory_policy', 'both_strategies_match_independent_answers',
            'machine_enrichment_preserves_unmatched_rows', 'duplicate_machine_dimension_rejected']


def run(spark, args) -> dict:
    """Alternate strategy order, retain every result/plan, and require equality for all paired measurements."""
    binding = json.loads(args.binding.read_text())
    policy = load_policy(args.policy)
    args.output.mkdir(parents=True, exist_ok=False)
    checks = known_answer_checks(spark)
    normalized = spark.read.parquet(str(args.source))
    machines = spark.read.parquet(str(args.machines))
    if machines.count() != binding['machine_count']:
        raise ValueError('machine snapshot count differs from source receipt')
    source = enrich_factory(normalized, machines)
    allowed = [row['factory_id'] for row in policy['factories']]
    invalid = source.filter(F.col('factory_id').isNull() | ~F.col('factory_id').isin(allowed) |
                            F.col('event_ts').isNull() | F.col('temperature_c').isNull() |
                            F.col('vibration_rms_mm_s').isNull() | F.col('operating_status').isNull() |
                            ~F.col('operating_status').isin('operating', 'failed', 'maintenance') |
                            F.col('source_manifest_sha256').isNull() |
                            (F.col('source_manifest_sha256') != binding['source_manifest_sha256']))
    if invalid.limit(1).count():
        raise ValueError('Silver contains missing/unknown keys, sensors, status or source provenance')
    counts = {row['factory_id']: row['count'] for row in source.groupBy('factory_id').count().collect()}
    if counts != binding['factory_unique_counts'] or sum(counts.values()) != binding['unique_rows']:
        raise ValueError('Silver row/factory counts differ from validated source')
    readings, dimension = prepare_readings(source), policy_frame(spark, policy)
    if readings.filter('temperature_milli IS NULL OR vibration_milli IS NULL').limit(1).count():
        raise ValueError('sensor cannot be represented as exact milli units')
    spark.sparkContext.setJobGroup('skew-partition-diagnostic', 'Actual row counts after factory-key repartition')
    diagnostic = (readings.repartition(args.partitions, 'factory_id').withColumn('partition_id', F.spark_partition_id())
                  .groupBy('partition_id').count())
    distribution = sorted([row.asDict() for row in diagnostic.collect()], key=lambda row: row['partition_id'])
    clear_group(spark.sparkContext)
    measures = []
    expected, expected_hash = None, None
    for repeat in range(args.repetitions):
        order = ('baseline', 'broadcast') if repeat % 2 == 0 else ('broadcast', 'baseline')
        for strategy in order:
            group = f'skew-{strategy}-{repeat}'
            spark.sparkContext.setJobGroup(group, f'{strategy}: factory-hour alarms, repeat {repeat}', interruptOnCancel=True)
            query = alarm_query(readings, dimension, strategy).limit(100_001)
            started = time.perf_counter()
            values = canonical_rows(query)
            elapsed = time.perf_counter() - started
            plan = query._jdf.queryExecution().executedPlan().toString()
            required = 'SortMergeJoin' if strategy == 'baseline' else 'BroadcastHashJoin'
            if required not in plan or (strategy == 'broadcast' and 'SortMergeJoin' in plan):
                raise ValueError(f'expected {required}, inspect actual plan: {plan}')
            payload = json.dumps(values, sort_keys=True, separators=(',', ':')).encode()
            result_hash = sha256(payload).hexdigest()
            if expected is None:
                expected, expected_hash = values, result_hash
            if values != expected or sum(row['reading_count'] for row in values) != binding['unique_rows']:
                raise ValueError('strategy changed the exact aggregate result or lost input rows')
            save(args.output / f'{group}.json', values)
            measure = {'group': group, 'strategy': strategy, 'repeat': repeat, 'seconds': elapsed,
                       'result_rows': len(values), 'result_sha256': result_hash, 'physical_plan': plan}
            measures.append(measure)
            save(args.report.parent / 'progress.json', {'checked_utc': utc_now(), 'completed_measurements': len(measures),
                                                       'latest': measure, 'application_id': spark.sparkContext.applicationId})
            print(json.dumps({key: value for key, value in measure.items() if key != 'physical_plan'}), flush=True)
            clear_group(spark.sparkContext)
    medians = {name: statistics.median(item['seconds'] for item in measures if item['strategy'] == name)
               for name in ('baseline', 'broadcast')}
    memory = {name: int((Path('/sys/fs/cgroup') / name).read_text()) for name in ('memory.current', 'memory.peak')}
    return {'passed': True, 'completed_utc': utc_now(), 'binding': binding, 'policy': policy,
            'checks': checks + ['silver_source_and_factory_counts', 'exact_integer_aggregate_equality_all_pairs',
                                 'actual_sort_merge_and_broadcast_plans', 'no_rows_lost_in_policy_join'],
            'factory_unique_counts': counts, 'factory_partition_counts': distribution,
            'max_partition_share': max(row['count'] for row in distribution) / binding['unique_rows'],
            'repetitions': args.repetitions, 'measurements': measures, 'median_seconds': medians,
            'observed_speedup': medians['baseline'] / medians['broadcast'], 'result_sha256': expected_hash,
            'application_id': spark.sparkContext.applicationId, 'spark_version': spark.version,
            'python': platform.python_version(), 'cgroup_memory_before_shutdown': memory,
            'spark_conf': {key: spark.conf.get(key) for key in ('spark.sql.shuffle.partitions',
                                                               'spark.sql.adaptive.enabled', 'spark.sql.autoBroadcastJoinThreshold')},
            'scope': 'Same immutable normalized Silver, shared bounded broadcast of machine-to-factory metadata, and alarm policy. Same resources/partitions, alternating uncached query pairs. AQE/automatic broadcast disabled for both to isolate the factory-policy join change. OS/file cache and platform services are not isolated. This is finalized offline analytics, not a point-in-time feature or ML label; no 100GB performance extrapolation.'}


def main() -> None:
    """Run the fixed query in the pinned Spark image and publish success only after every comparison passes."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'machines', 'binding', 'policy', 'output', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--partitions', type=int, default=32)
    parser.add_argument('--repetitions', type=int, default=3)
    args = parser.parse_args()
    if not 2 <= args.partitions <= 64 or not 1 <= args.repetitions <= 5 or args.output.exists() or args.report.exists():
        raise ValueError('invalid bounded configuration or output already exists')
    spark = (SparkSession.builder.appName('iot-factory-skew-benchmark').config('spark.sql.session.timeZone', 'UTC')
             .config('spark.sql.shuffle.partitions', args.partitions).config('spark.sql.adaptive.enabled', 'false')
             .config('spark.sql.autoBroadcastJoinThreshold', '-1').getOrCreate())
    spark.sparkContext.setLogLevel('WARN')
    try:
        report = run(spark, args)
        save(args.report, report)
        save(args.output / '_RUN_SUCCESS.json', {'report_sha256': sha256(args.report.read_bytes()).hexdigest()})
        print('PASS: all paired skew results and executed join plans verified', flush=True)
    finally:
        spark.stop()


if __name__ == '__main__':
    main()
