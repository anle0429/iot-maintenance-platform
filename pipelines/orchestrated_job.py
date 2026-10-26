"""Execute one real Spark ingest or validation stage for the three Airflow data pipelines."""

import argparse
import json
from pathlib import Path
import platform
import time

from pyspark.sql import SparkSession, functions as F

from generator.config import iso_ts
from pipelines.chunk_ingest import PAYLOAD, exact_rows_equal, latest_readings, read_table, write_parquet
from pipelines.chunk_source import ChunkSource
from pipelines.features import compute_features, label_snapshots
from pipelines.orchestration_contract import (STAGES, TABLES, digest, inventory, save, stage_path,
                                               utc_now, verified_manifest)
from pipelines.spark_checks import run_spark_checks
from pipelines.transforms import build_dim_machine, temporal_fact


def load(spark, directory: Path, table: str):
    """Read a single immutable typed Parquet table."""
    return spark.read.parquet(str(directory / table))


def ingest(spark, root: Path, batch: str, stage: str, fault: str) -> dict:
    """Materialize a new stage once; a retry verifies and reuses identical completed bytes."""
    source = ChunkSource(root / 'data/source/orchestration-pilot', root / 'outputs/orchestration/source-receipt.json')
    target = stage_path(root, batch, stage)
    upstream = None
    if stage != 'dp1':
        previous = stage_path(root, batch, STAGES[STAGES.index(stage) - 1])
        upstream = verified_manifest(previous)
        if upstream['source_manifest_sha256'] != source.manifest_sha256:
            raise ValueError('upstream belongs to a different source')
    binding = {'batch': batch, 'stage': stage, 'source_manifest_sha256': source.manifest_sha256,
               'receipt_sha256': source.receipt_sha256, 'fault': fault,
               'upstream_manifest_sha256': digest(previous / '_INGESTED.json') if upstream else None,
               'transformation_sha256': [{'path': name, 'sha256': digest(root / 'pipelines' / name)} for name in
                                        ('orchestrated_job.py', 'orchestration_contract.py', 'chunk_ingest.py',
                                         'chunk_source.py', 'transforms.py', 'features.py', 'spark_checks.py')]}
    if target.exists():
        existing = verified_manifest(target, require_validated=False)
        if any(existing.get(key) != value for key, value in binding.items()):
            raise ValueError('retry input differs from completed stage')
        print('Replay: verified existing materialization; no append or overwrite', flush=True)
        return existing
    target.mkdir(parents=True)
    tables = {}

    def write(frame, name: str) -> None:
        """Persist typed tables and record the actual readback schema and row count."""
        write_parquet(frame, target / name, 4 if name.endswith('telemetry') else 1)
        back = load(spark, target, name)
        tables[name] = {'rows': back.count(), 'schema': back.schema.jsonValue()}

    known = {}
    if stage == 'dp1':
        raw = read_table(spark, source, 'v1').unionByName(read_table(spark, source, 'v2'))
        if fault:
            if fault != 'null_machine' or not batch.startswith('fixture-'):
                raise ValueError('fault injection is allowed only for fixture-* batches')
            first_id = raw.agg(F.min('event_id')).first()[0]
            raw = raw.withColumn('machine_id', F.when(F.col('event_id') == first_id, F.lit(None)).otherwise(F.col('machine_id')))
        write(raw, 'bronze/raw_telemetry')
        for name in ('machines', 'failures', 'maintenance'):
            write(read_table(spark, source, name), f'bronze/raw_{name}')
    elif stage == 'dp2':
        raw = load(spark, previous, 'bronze/raw_telemetry')
        readings = latest_readings(raw).cache()
        machines = load(spark, previous, 'bronze/raw_machines')
        failures = load(spark, previous, 'bronze/raw_failures')
        maintenance = load(spark, previous, 'bronze/raw_maintenance')
        dimension = build_dim_machine(machines.withColumn('recorded_ts', F.col('valid_from_ts')), iso_ts(source.config.start)).cache()
        write(readings, 'silver/stg_telemetry')
        write(failures, 'silver/stg_failures')
        write(maintenance, 'silver/stg_maintenance')
        write(dimension, 'gold/dim_machine')
        write(dimension.select('factory_id').distinct(), 'gold/dim_factory')
        for frame, time_col, name in [(readings, 'event_ts', 'telemetry'), (failures, 'failure_start_ts', 'failure'),
                                       (maintenance, 'start_ts', 'maintenance')]:
            write(temporal_fact(frame, dimension, time_col), f'gold/fact_{name}')
    else:
        known = run_spark_checks(spark)
        readings = load(spark, previous, 'silver/stg_telemetry').cache()
        failures = load(spark, previous, 'silver/stg_failures')
        maintenance = load(spark, previous, 'silver/stg_maintenance')
        machines = load(spark, previous, 'gold/dim_machine').filter('is_current')
        snapshots = (readings.filter(F.pmod(F.unix_timestamp('event_ts') - int(source.config.start.timestamp()), F.lit(300)) == 0)
                     .select('machine_id', F.col('event_ts').alias('event_timestamp'), 'operating_status').cache())
        write(compute_features(snapshots.select('machine_id', 'event_timestamp'), readings, machines, failures), 'features/feat_machine_10m')
        write(label_snapshots(snapshots, failures, maintenance, iso_ts(source.config.end)), 'features/label_failure_1h')
    source.unchanged()
    result = {**binding, 'created_utc': utc_now(), 'tables': tables, 'files': inventory(target), 'known_answer_checks': known,
              'config': source.config.to_dict(), 'spark': spark.version, 'python': platform.python_version(),
              'application_id': spark.sparkContext.applicationId,
              'code_sha256': [{'path': path.relative_to(root).as_posix(), 'sha256': digest(path)}
                              for folder in ('generator', 'pipelines') for path in sorted((root / folder).glob('*.py'))]}
    save(target / '_INGESTED.json', result)
    return result


def validate(spark, root: Path, batch: str, stage: str) -> dict:
    """Read persisted bytes and record every contract outcome before returning a failure."""
    target = stage_path(root, batch, stage)
    manifest = verified_manifest(target, require_validated=False)
    source = ChunkSource(root / 'data/source/orchestration-pilot', root / 'outputs/orchestration/source-receipt.json')
    checks = []

    def check(table: str, name: str, observed, expected=True) -> None:
        """Keep metrics and expectations next to each pass/fail result for external assertions."""
        checks.append({'table': table, 'name': name, 'observed': observed, 'expected': expected, 'passed': observed == expected})

    def no_bad(frame, expression: str) -> bool:
        """Check a data rule with a bounded existence query, including null handling in SQL."""
        return frame.filter(expression).limit(1).count() == 0

    def exact(table: str, frame, columns=None) -> None:
        """Compare full multisets rather than accepting count equality alone."""
        try:
            exact_rows_equal(frame, load(spark, target, table), columns or frame.columns)
            check(table, 'exact_multiset_readback', True)
        except ValueError:
            check(table, 'exact_multiset_readback', False)

    for table in TABLES[stage]:
        actual = load(spark, target, table)
        check(table, 'persisted_row_count', actual.count(), manifest['tables'][table]['rows'])
        check(table, 'persisted_schema', actual.schema.jsonValue(), manifest['tables'][table]['schema'])
    if stage == 'dp1':
        table = 'bronze/raw_telemetry'
        raw = load(spark, target, table).cache()
        original = read_table(spark, source, 'v1').unionByName(read_table(spark, source, 'v2'))
        exact(table, original)
        check(table, 'catalog_count', raw.count(), sum(source.manifest['tables'][key]['rows'] for key in ('v1', 'v2')))
        check(table, 'required_fields_and_times', no_bad(raw, 'event_id IS NULL OR machine_id IS NULL OR event_ts IS NULL OR received_ts IS NULL OR created_ts IS NULL OR received_ts < event_ts OR created_ts < received_ts OR temperature_c IS NULL'))
        check(table, 'firmware_contract', no_bad(raw, 'schema_version IS NULL OR schema_version NOT IN (1,2) OR (schema_version=1 AND pressure_bar IS NOT NULL) OR (schema_version=2 AND pressure_bar IS NULL)'))
        machines = load(spark, target, 'bronze/raw_machines')
        check(table, 'machine_foreign_key', raw.join(machines.select('machine_id').distinct(), 'machine_id', 'left_anti').limit(1).count(), 0)
        for name in ('machines', 'failures', 'maintenance'):
            exact(f'bronze/raw_{name}', read_table(spark, source, name))
        check(table, 'source_manifest_binding', manifest['source_manifest_sha256'], source.manifest_sha256)
    elif stage == 'dp2':
        previous = stage_path(root, batch, 'dp1')
        verified_manifest(previous)
        silver = load(spark, target, 'silver/stg_telemetry').cache()
        raw = load(spark, previous, 'bronze/raw_telemetry')
        exact('silver/stg_telemetry', latest_readings(raw))
        check('silver/stg_telemetry', 'expected_unique_events', silver.count(), source.config.unique_rows)
        check('silver/stg_telemetry', 'unique_event_id', silver.select('event_id').distinct().count(), silver.count())
        check('silver/stg_telemetry', 'replay_idempotent', latest_readings(raw.unionByName(raw)).count(), silver.count())
        check('silver/stg_telemetry', 'latest_created_export', silver.select('event_id', 'created_ts').join(raw.groupBy('event_id').agg(F.max('created_ts').alias('latest')), 'event_id').filter('created_ts != latest').count(), 0)
        dimension = load(spark, target, 'gold/dim_machine')
        check('gold/dim_machine', 'one_current_version', dimension.filter('is_current').select('machine_id').distinct().count(), source.config.machine_count)
        check('gold/dim_machine', 'valid_scd_intervals', no_bad(dimension, 'machine_sk IS NULL OR factory_id IS NULL OR valid_from_ts IS NULL OR valid_to_ts <= valid_from_ts OR recorded_ts < valid_from_ts'))
        for name, origin in [('telemetry', silver), ('failure', load(spark, previous, 'bronze/raw_failures')),
                              ('maintenance', load(spark, previous, 'bronze/raw_maintenance'))]:
            table = f'gold/fact_{name}'
            fact = load(spark, target, table)
            check(table, 'dimension_join_preserves_rows', fact.count(), origin.count())
            check(table, 'dimension_foreign_key', no_bad(fact, 'machine_sk IS NULL OR factory_id IS NULL'))
        for name in ('failures', 'maintenance'):
            exact(f'silver/stg_{name}', load(spark, previous, f'bronze/raw_{name}'))
        exact('gold/dim_factory', dimension.select('factory_id').distinct())
    else:
        previous = stage_path(root, batch, 'dp2')
        verified_manifest(previous)
        features = load(spark, target, 'features/feat_machine_10m').cache()
        labels = load(spark, target, 'features/label_failure_1h').cache()
        table = 'features/feat_machine_10m'
        check(table, 'expected_five_minute_grid', features.count(), source.config.unique_rows * source.config.sample_seconds // 300)
        check(table, 'machine_time_grain', features.groupBy('machine_id', 'event_timestamp').count().filter('count != 1').count(), 0)
        check(table, 'event_and_arrival_availability', no_bad(features, 'max_received_ts > event_timestamp OR max_feature_event_ts > event_timestamp OR created IS NULL OR event_timestamp IS NULL'))
        check(table, 'label_columns_excluded', not any('label' in col or col == 'failure_in_next_1h' for col in features.columns))
        check('features/label_failure_1h', 'labels_match_grid', labels.count(), features.count())
        check('features/label_failure_1h', 'ineligible_is_unknown', no_bad(labels, "(label_status != 'eligible' AND failure_in_next_1h IS NOT NULL) OR (label_status='eligible' AND failure_in_next_1h IS NULL)"))
        check('features/label_failure_1h', 'future_failure_interval', no_bad(labels, "failure_in_next_1h=1 AND (next_failure_ts <= event_timestamp OR next_failure_ts > label_end_ts OR operating_status != 'operating')"))
        check('features/label_failure_1h', 'tail_is_censored', no_bad(labels, f"label_status='eligible' AND label_end_ts >= TIMESTAMP '{iso_ts(source.config.end)}'"))
        check(table, 'known_answer_oracles', bool(manifest['known_answer_checks']) and all(manifest['known_answer_checks'].values()))
        # Independently recompute one nonempty persisted vector from its actual arrived source readings.
        sample = features.filter('reading_count_10m >= 5').orderBy('event_timestamp', 'machine_id').first()
        readings = load(spark, previous, 'silver/stg_telemetry')
        if sample:
            from datetime import timedelta
            contributing = readings.filter((F.col('machine_id') == sample.machine_id) & (F.col('event_ts') > sample.event_timestamp - timedelta(minutes=10)) & (F.col('event_ts') <= sample.event_timestamp) & (F.col('received_ts') <= sample.event_timestamp))
            actual = contributing.agg(F.count('*'), F.avg('temperature_c')).first()
            check(table, 'independent_source_trace_count', actual[0], sample.reading_count_10m)
            check(table, 'independent_source_trace_mean', abs(actual[1] - sample.temperature_mean_10m) < 1e-9)
        else:
            check(table, 'independent_source_trace_count', False)
    source.unchanged()
    result = {'passed': all(c['passed'] for c in checks), 'batch': batch, 'stage': stage, 'validated_utc': utc_now(),
              'manifest_sha256': digest(target / '_INGESTED.json'), 'application_id': spark.sparkContext.applicationId,
              'checks': checks, 'source_manifest_sha256': source.manifest_sha256}
    save(target / 'validation.json', result)
    return result


def main() -> int:
    """Expose only the stage and safe batch ID; source/storage paths are fixed by the project contract."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=STAGES, required=True)
    parser.add_argument('--operation', choices=('ingest', 'validate'), required=True)
    parser.add_argument('--batch', required=True)
    parser.add_argument('--fault', choices=('', 'null_machine'), default='')
    args = parser.parse_args()
    root = Path.cwd().resolve()
    stage_path(root, args.batch, args.stage)
    started = time.perf_counter()
    spark = SparkSession.builder.appName(f'iot-{args.stage}-{args.operation}-{args.batch}').getOrCreate()
    spark.conf.set('spark.sql.session.timeZone', 'UTC')
    spark.sparkContext.setLogLevel('WARN')
    try:
        result = ingest(spark, root, args.batch, args.stage, args.fault) if args.operation == 'ingest' else validate(spark, root, args.batch, args.stage)
        print(json.dumps({'stage': args.stage, 'operation': args.operation, 'passed': result.get('passed', True), 'seconds': time.perf_counter() - started}), flush=True)
        return 0 if result.get('passed', True) else 2
    finally:
        spark.stop()


if __name__ == '__main__':
    raise SystemExit(main())
