"""Compare equivalent schema readers and dedup winner selectors on one immutable, bounded telemetry source."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import statistics
import time


FIELDS = [(name, 'string') for name in ('event_id', 'machine_id', 'operating_status')]
FIELDS += [(name, 'timestamp') for name in ('event_ts', 'received_ts', 'created_ts')]
FIELDS += [(name, 'double') for name in ('temperature_c', 'vibration_rms_mm_s', 'current_a', 'rpm', 'pressure_bar')]
FIELDS += [('schema_version', 'int')]
COLUMNS = [name for name, _ in FIELDS]
DEDUP_COLUMNS = COLUMNS + ['source_path', 'source_line']


def save(path, value):
    """Write a new finite structured receipt with stable LF encoding."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8', newline='\n')


def normalized_record(value):
    """Independently type one source record, preserving unknown v1 pressure rather than imputing a value."""
    version = value.get('schema_version')
    if version not in (1, 2):
        raise ValueError('unsupported schema version')
    required = set(COLUMNS) - ({'pressure_bar'} if version == 1 else set())
    if set(value) != required or (version == 2 and value['pressure_bar'] is None):
        raise ValueError('source fields do not match their declared version')
    result = {}
    for name, kind in FIELDS:
        item = value.get(name)
        if name == 'pressure_bar' and version == 1:
            result[name] = None
        elif kind == 'timestamp':
            stamp = datetime.fromisoformat(item.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('source timestamps must include their UTC offset')
            result[name] = stamp.astimezone(timezone.utc).replace(tzinfo=None)
        elif kind == 'double':
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
                raise ValueError('invalid finite sensor value')
            result[name] = float(item)
        elif kind == 'int':
            result[name] = int(item)
        else:
            if not isinstance(item, str) or not item:
                raise ValueError('empty telemetry key/status')
            result[name] = item
    if not result['event_ts'] <= result['received_ts'] <= result['created_ts']:
        raise ValueError('invalid telemetry availability order')
    return result


def inspect_source(source, envelope=None):
    """Authenticate two small JSONL partitions and independently resolve the exact latest-export/tie contract."""
    source = source.resolve()
    manifest_path = source / 'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    files = [row for row in manifest['files'] if row['path'].startswith('telemetry/')]
    if {row['path'] for row in files} != {'telemetry/schema_v1/part-00000.jsonl', 'telemetry/schema_v2/part-00000.jsonl'}:
        raise ValueError('use the original bounded two-partition generator source')
    if sum(row['bytes'] for row in files) > 200_000_000 or sum(row['rows'] for row in files) > 100_000:
        raise ValueError('this correctness/timing study is bounded to 100,000 exports and 200 MB')
    records, winners, immutable = [], {}, {}
    output = envelope.open('x', encoding='utf-8', newline='\n') if envelope else None
    try:
        for row in sorted(files, key=lambda item: item['path']):
            path = source / row['path']
            if not path.resolve().is_relative_to(source) or path.is_symlink():
                raise ValueError('unsafe source partition')
            body = path.read_bytes()
            if len(body) != row['bytes'] or sha256(body).hexdigest() != row['sha256']:
                raise ValueError('source partition differs from manifest')
            lines = body.decode('utf-8').splitlines()
            if len(lines) != row['rows']:
                raise ValueError('source row count differs from manifest')
            for number, line in enumerate(lines, start=1):
                record = normalized_record(json.loads(line))
                identifier = record['event_id']
                payload = {name: record[name] for name in COLUMNS if name != 'created_ts'}
                if identifier in immutable and immutable[identifier] != payload:
                    raise ValueError('conflicting duplicate telemetry payload')
                immutable[identifier] = payload
                record.update(source_path=row['path'], source_line=number)
                records.append(record)
                previous = winners.get(identifier)
                if (previous is None or record['created_ts'] > previous['created_ts'] or
                    (record['created_ts'] == previous['created_ts'] and
                     (record['source_path'], -record['source_line']) < (previous['source_path'], -previous['source_line']))):
                    winners[identifier] = record
                if output:
                    output.write(json.dumps({'payload': line, 'source_path': row['path'], 'source_line': number},
                                            separators=(',', ':'), allow_nan=False) + '\n')
    finally:
        if output:
            output.close()
    if not records or {r['schema_version'] for r in records} != {1, 2}:
        raise ValueError('both schema versions must be observed')
    inventory = {'manifest_sha256': sha256(manifest_path.read_bytes()).hexdigest(),
                 'files': sorted(files, key=lambda item: item['path']),
                 'rows': len(records), 'unique_events': len(winners), 'extra_exports': len(records) - len(winners)}
    return inventory, records, list(winners.values())


def spark_schema(metadata=False):
    """Use the unchanged production field declaration, and reject accidental divergence in the benchmark contract."""
    from pyspark.sql import types as T
    from pipelines.transforms import TELEMETRY_FIELDS, struct_schema
    if TELEMETRY_FIELDS != FIELDS:
        raise ValueError('production telemetry schema changed')
    result = struct_schema(FIELDS)
    if metadata:
        result = result.add('source_path', T.StringType(), False).add('source_line', T.LongType(), False)
    return result


def schema_reader(spark, paths, strategy):
    """Compare full-source inference with an explicit union schema, then apply the identical typed projection."""
    from pyspark.sql import functions as F
    reader = spark.read.option('mode', 'FAILFAST')
    if strategy == 'inferred':
        frame = reader.option('samplingRatio', 1.0).option('inferTimestamp', 'false').json(paths)
    elif strategy == 'declared':
        frame = reader.schema(spark_schema()).json(paths)
    else:
        raise ValueError('unknown schema strategy')
    return frame.select(*[(F.col(name) if name in frame.columns else F.lit(None)).cast(kind).alias(name)
                          for name, kind in FIELDS])


def validate_dedup_input(frame):
    """Reject null ordering keys and conflicting immutable readings before either timed winner-selection strategy."""
    from pyspark.sql import functions as F
    if frame.filter('event_id IS NULL OR created_ts IS NULL OR source_path IS NULL OR source_line IS NULL OR source_line < 1').limit(1).count():
        raise ValueError('dedup ordering keys must be non-null and source_line positive')
    payload = [name for name in COLUMNS if name != 'created_ts']
    conflicts = (frame.withColumn('_payload_hash', F.sha2(F.to_json(F.struct(*payload), {'ignoreNullFields': 'false'}), 256))
                 .groupBy('event_id').agg(F.countDistinct('_payload_hash').alias('variants')).filter('variants > 1'))
    if conflicts.limit(1).count():
        raise ValueError('conflicting duplicate telemetry payload')


def select_winners(frame, strategy):
    """Preserve latest created time, ascending source path and descending physical source-line tie breaks."""
    from pyspark.sql import Window, functions as F
    if strategy == 'window':
        order = Window.partitionBy('event_id').orderBy(F.col('created_ts').desc(), F.col('source_path'), F.col('source_line').desc())
        return frame.withColumn('_rank', F.row_number().over(order)).filter('_rank = 1').drop('_rank')
    if strategy == 'aggregate':
        # Ordering keys are validated non-null. A fully tied order has identical payload/provenance,
        # so min_by's arbitrary choice between exact duplicate rows cannot change the result.
        order = F.struct(-F.unix_micros('created_ts'), F.col('source_path'), -F.col('source_line'))
        return frame.groupBy('event_id').agg(F.min_by(F.struct(*DEDUP_COLUMNS), order).alias('_winner')).select('_winner.*')
    raise ValueError('unknown dedup strategy')


def full_summary(frame, columns, return_query=False):
    """Force every result column through SHA-256 and optionally retain the exact executed materialization query."""
    from pyspark.sql import functions as F
    serialized = F.to_json(F.struct(*columns), {'ignoreNullFields': 'false', 'timestampFormat': "yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'"})
    hashes = frame.select(F.sha2(serialized, 256).alias('_hash'))
    sums = [F.sum(F.conv(F.substring('_hash', 1 + part * 16, 16), 16, 10).cast('decimal(38,0)')).alias('sum_' + str(part))
            for part in range(4)]
    query = hashes.agg(F.count('*').alias('rows'), F.min('_hash').alias('minimum_row_sha256'),
                       F.max('_hash').alias('maximum_row_sha256'), *sums)
    # The global aggregate returns exactly one bounded row; collect executes this same
    # query object, allowing the report to bind its actual plan after the timed action.
    row = query.collect()[0].asDict()
    result = {key: str(value) if key.startswith('sum_') else value for key, value in row.items()}
    result['summary_sha256'] = sha256(json.dumps(result, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return (result, query) if return_query else result


def exact_equal(left, right, columns):
    """Check both multiset differences exactly; hash summaries alone are never the equivalence gate."""
    left, right = left.select(*columns), right.select(*columns)
    if left.exceptAll(right).limit(1).count() or right.exceptAll(left).limit(1).count():
        raise ValueError('full result multiset differs from independent expected rows')


def group(spark, name, description):
    """Assign source-visible groups to actual jobs for later event-log attribution."""
    spark.sparkContext.setJobGroup(name, description, interruptOnCancel=True)


def clear_group(spark):
    """Clear local group properties after an action without depending on an unavailable Python convenience API."""
    for name in ('spark.jobGroup.id', 'spark.job.description', 'spark.job.interruptOnCancel'):
        spark.sparkContext.setLocalProperty(name, None)


def known_answer_checks(spark):
    """Exercise latest-export ties, immutable conflicts and unknown pressure against hand-computed answers."""
    from pipelines.transforms import deduplicate_telemetry
    stamp = datetime(2026, 10, 1)
    base = dict(event_id='A', machine_id='P-001', operating_status='operating', event_ts=stamp,
                received_ts=stamp, created_ts=stamp, temperature_c=10.0, vibration_rms_mm_s=1.0,
                current_a=2.0, rpm=100.0, pressure_bar=None, schema_version=1,
                source_path='a.jsonl', source_line=1)
    newest = stamp.replace(second=1, microsecond=1)
    rows = [base, {**base, 'created_ts': newest, 'source_path': 'z.jsonl', 'source_line': 100},
            {**base, 'created_ts': newest}, {**base, 'created_ts': newest, 'source_line': 9},
            {**base, 'event_id': 'B', 'pressure_bar': 5.0, 'schema_version': 2}]
    frame = spark.createDataFrame(rows, spark_schema(metadata=True))
    expected = spark.createDataFrame([rows[3], rows[4]], spark_schema(metadata=True))
    validate_dedup_input(frame)
    for method in ('window', 'aggregate'):
        exact_equal(select_winners(frame, method), expected, DEDUP_COLUMNS)
    exact_equal(deduplicate_telemetry(frame), expected, DEDUP_COLUMNS)
    conflicting = spark.createDataFrame(rows + [{**base, 'temperature_c': 11.0}], spark_schema(metadata=True))
    try:
        validate_dedup_input(conflicting)
    except ValueError as error:
        if 'conflicting duplicate' not in str(error):
            raise
    else:
        raise ValueError('conflicting payload fixture was accepted')
    invalid = spark.createDataFrame([{**base, 'created_ts': None}], spark_schema(metadata=True))
    try:
        validate_dedup_input(invalid)
    except ValueError:
        pass
    else:
        raise ValueError('null dedup ordering key was accepted')
    return ['latest_export_microsecond_order', 'source_path_ascending_tie', 'source_line_descending_tie',
            'both_selectors_match_independent_rows_and_original_runner', 'immutable_conflict_rejected',
            'null_ordering_key_rejected', 'v1_unknown_pressure_not_imputed', 'v2_pressure_retained']


def run(spark, args):
    """Measure three alternating pairs per experiment, retaining every outcome and exact correctness check."""
    from pyspark import StorageLevel
    from pyspark.sql import functions as F, types as T
    from pipelines.transforms import parse_records
    args.output.mkdir(parents=True, exist_ok=False)
    envelope = args.output / 'source-envelopes.jsonl'
    inventory, records, winners = inspect_source(args.source, envelope)
    paths = [str(args.source / row['path']) for row in inventory['files']]
    checks = known_answer_checks(spark)
    expected_schema = spark.createDataFrame(records, spark_schema())
    expected_dedup = spark.createDataFrame(winners, spark_schema(metadata=True))
    schema_expected_summary = full_summary(expected_schema, COLUMNS)
    dedup_expected_summary = full_summary(expected_dedup, DEDUP_COLUMNS)

    # Preserve a visibly broken v1-only reader separately from the equivalent timing pair.
    v1_schema = spark.read.option('mode', 'FAILFAST').json(paths[0]).schema
    legacy = spark.read.option('mode', 'FAILFAST').schema(v1_schema).json(paths)
    lost = legacy.filter('schema_version=2').count() if 'pressure_bar' not in legacy.columns else 0
    if lost != sum(r['schema_version'] == 2 for r in records):
        raise ValueError('v1-only failure demonstration did not expose all missing v2 pressure values')
    legacy_failure = {'pressure_column_missing': 'pressure_bar' not in legacy.columns,
                      'v2_exports_with_lost_pressure': lost, 'rows': legacy.count(), 'timed_as_equivalent': False}
    raw_schema = T.StructType([T.StructField('payload', T.StringType(), False),
                              T.StructField('source_path', T.StringType(), False), T.StructField('source_line', T.LongType(), False)])
    raw = spark.read.schema(raw_schema).option('mode', 'FAILFAST').json(str(envelope))
    shared = parse_records(raw, FIELDS).select(*DEDUP_COLUMNS).persist(StorageLevel.MEMORY_AND_DISK)
    try:
        full_summary(shared, DEDUP_COLUMNS)
        validate_dedup_input(shared)
        measurements, warmups = [], []
        factories = {'schema': [('inferred', lambda: schema_reader(spark, paths, 'inferred')),
                                ('declared', lambda: schema_reader(spark, paths, 'declared'))],
                     'dedup': [('window', lambda: select_winners(shared, 'window')),
                               ('aggregate', lambda: select_winners(shared, 'aggregate'))]}
        for experiment, methods in factories.items():
            columns = COLUMNS if experiment == 'schema' else DEDUP_COLUMNS
            expected = expected_schema if experiment == 'schema' else expected_dedup
            expected_summary = schema_expected_summary if experiment == 'schema' else dedup_expected_summary
            for strategy, factory in methods:
                name = f'corrections-warmup-{experiment}-{strategy}'
                group(spark, name, 'Unmeasured warmup: ' + name)
                frame = factory()
                summary = full_summary(frame, columns)
                clear_group(spark)
                if summary != expected_summary:
                    raise ValueError('warmup full-column summary differs from independent source oracle')
                exact_equal(frame, expected, columns)
                warmups.append({'group': name, 'experiment': experiment, 'strategy': strategy, 'summary': summary})
            for repeat in range(args.repetitions):
                order = methods if repeat % 2 == 0 else list(reversed(methods))
                for strategy, factory in order:
                    name = f'corrections-{experiment}-{strategy}-{repeat}'
                    group(spark, name, f'{experiment}: {strategy}, paired repeat {repeat}')
                    started = time.perf_counter()
                    frame = factory()
                    summary, materialization = full_summary(frame, columns, return_query=True)
                    elapsed = time.perf_counter() - started
                    plan = materialization._jdf.queryExecution().executedPlan().toString()
                    selector_plan = frame._jdf.queryExecution().executedPlan().toString()
                    clear_group(spark)
                    if summary != expected_summary:
                        raise ValueError('measured full-column summary differs from independent source oracle')
                    # Validation recomputations are outside the measured group/timing window.
                    exact_equal(frame, expected, columns)
                    if experiment == 'dedup' and (('Window' not in plan) if strategy == 'window' else ('min_by' not in plan)):
                        raise ValueError('dedup executed plan does not contain its selected strategy')
                    measure = {'group': name, 'experiment': experiment, 'strategy': strategy, 'repeat': repeat,
                               'seconds': elapsed, 'result_sha256': summary['summary_sha256'],
                               'result_rows': summary['rows'], 'summary': summary, 'physical_plan': plan,
                               'selector_plan': selector_plan,
                               'exact_multiset_matches_python_oracle': True}
                    measurements.append(measure)
                    save(args.output / (name + '.json'), measure)
                    print(json.dumps({key: value for key, value in measure.items() if key not in ('physical_plan', 'selector_plan', 'summary')}), flush=True)
        exact_equal(select_winners(shared, 'aggregate'), expected_dedup, DEDUP_COLUMNS)
        pressure = (shared.groupBy('schema_version').agg(F.count('*').alias('exports'),
                    F.sum(F.col('pressure_bar').isNotNull().cast('long')).alias('pressure_present')).orderBy('schema_version'))
        pressure_rows = [row.asDict() for row in pressure.collect()]
    finally:
        shared.unpersist(blocking=True)
        clear_group(spark)
    after, _, _ = inspect_source(args.source)
    if after != inventory:
        raise ValueError('source files changed during the benchmark')
    medians = {experiment: {strategy: statistics.median(row['seconds'] for row in measurements
                           if row['experiment'] == experiment and row['strategy'] == strategy)
                           for strategy, _ in methods} for experiment, methods in factories.items()}
    ratios = {'schema': medians['schema']['inferred'] / medians['schema']['declared'],
              'dedup': medians['dedup']['window'] / medians['dedup']['aggregate']}
    return {'passed': True, 'completed_utc': datetime.now(timezone.utc).isoformat(), 'input': inventory,
            'input_unchanged': True, 'schema_versions': pressure_rows, 'legacy_v1_only_failure': legacy_failure,
            'checks': checks + ['all_source_hashes_unchanged', 'full_schema_multiset_matches_python_oracle',
                               'all_dedup_winners_match_python_latest_export_oracle', 'equal_pressure_and_null_semantics'],
            'application_id': spark.sparkContext.applicationId, 'spark_version': spark.version,
            'repetitions': args.repetitions, 'warmups': warmups, 'measurements': measurements,
            'median_seconds': medians, 'observed_baseline_over_candidate_ratio': ratios,
            'spark_conf': {key: spark.conf.get(key) for key in ('spark.sql.shuffle.partitions', 'spark.sql.adaptive.enabled',
                                                               'spark.sql.autoBroadcastJoinThreshold')},
            'scope': 'Small original two-partition source. Schema pairs include full JSON inference/reader construction and all-column action; OS/file caches are uncontrolled. Dedup pairs compare winner selection on the same warmed normalized cache after shared key/conflict validation; they exclude parsing and conflict validation. One warmup per strategy, alternating paired order, all measurements retained. Aggregate is an experimental candidate, not a changed production DP2 implementation. Hash summaries force column work; exact multiset differences and independent Python expected rows establish correctness. No speed threshold, full100GB extrapolation or end-to-end pipeline speed claim.'}


def main():
    """Execute the finite native Spark study and publish a success marker only after all correctness gates pass."""
    from pyspark.sql import SparkSession
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'output', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--partitions', type=int, default=8)
    parser.add_argument('--repetitions', type=int, default=3)
    args = parser.parse_args()
    if not 2 <= args.partitions <= 32 or not 1 <= args.repetitions <= 5 or args.output.exists() or args.report.exists():
        raise ValueError('use bounded settings and fresh output/report paths')
    spark = (SparkSession.builder.appName('iot-data-corrections-benchmark')
             .config('spark.sql.session.timeZone', 'UTC').config('spark.sql.shuffle.partitions', args.partitions)
             .config('spark.sql.adaptive.enabled', 'false').config('spark.sql.autoBroadcastJoinThreshold', '-1').getOrCreate())
    spark.sparkContext.setLogLevel('WARN')
    try:
        report = run(spark, args)
        save(args.report, report)
        save(args.output / '_RUN_SUCCESS.json', {'report_sha256': sha256(args.report.read_bytes()).hexdigest()})
        print('PASS: equivalent schema/dedup pairs, exact oracle comparisons and preserved inputs', flush=True)
    finally:
        spark.stop()


if __name__ == '__main__':
    main()
