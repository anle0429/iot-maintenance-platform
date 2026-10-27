"""Build immutable protocol-bound features, censored labels and a purged ML cohort from verified Silver."""

import argparse
from datetime import datetime, timedelta, timezone
from functools import reduce
import json
from pathlib import Path
import platform
import time

from pyspark.sql import SparkSession, functions as F

from generator.config import parse_ts
from ml.protocol import PROTOCOL_SHA256, class_support, load_protocol
from pipelines.bounded_features import compute_features, label_snapshots
from pipelines.chunk_ingest import codec_inventory, exact_rows_equal, save_json, write_parquet
from pipelines.features import with_temporal_split
from pipelines.orchestration_contract import digest, inventory, save
from pipelines.transforms import require

ROOT = Path(__file__).resolve().parents[1]


def cohort(frame, protocol: dict):
    """Assign mutually exclusive exclusion reasons without using outcomes to tune eligibility."""
    columns = protocol['feature_allowlist']
    finite = reduce(lambda a, b: a & b, [F.col(c).isNotNull() & ~F.isnan(F.col(c).cast('double'))
                    & (F.abs(F.col(c).cast('double')) < F.lit(float('inf'))) for c in columns])
    split = protocol['chronological_split']
    result = with_temporal_split(frame, split['train_end_exclusive'], split['validation_end_exclusive'])
    reason = (F.when(F.col('label_status') != 'eligible', F.col('label_status'))
              .when(F.col('reading_count_10m') < protocol['cohort']['minimum_arrived_readings_10m'], 'no_arrived_readings')
              .when(~finite, 'nonfinite_feature').when(F.col('split').isNull(), 'purged')
              .otherwise('included'))
    return result.withColumn('cohort_reason', reason)


def cohort_checks(spark, protocol: dict) -> dict:
    """Check inclusion/purge/exclusion precedence on manually specified boundary answers."""
    train = parse_ts(protocol['chronological_split']['train_end_exclusive'])
    rows = [('included', train, train, 'eligible', 1, 1.0),
            ('purged', train.replace(day=train.day - 1), train, 'eligible', 1, 1.0),
            ('no_arrived_readings', train, train, 'eligible', 0, None),
            ('nonfinite_feature', train, train, 'eligible', 1, float('nan')),
            ('stopped', train, train, 'stopped', 0, None),
            ('censored', train, train, 'censored', 1, 1.0)]
    frame = spark.createDataFrame(rows, 'expected string, event_timestamp timestamp, label_available_ts timestamp, label_status string, reading_count_10m long, value double')
    for col in protocol['feature_allowlist']:
        if col != 'reading_count_10m':
            frame = frame.withColumn(col, F.col('value'))
    actual = cohort(frame, protocol).select('expected', 'cohort_reason', 'split').collect()
    checks = {'cohort_exclusion_boundaries': all(r.expected == r.cohort_reason for r in actual),
              'availability_equal_cut_is_purged': next(r for r in actual if r.expected == 'purged').split is None,
              'event_at_cut_enters_validation': next(r for r in actual if r.expected == 'included').split == 'validation'}
    require(all(checks.values()), f'cohort known answers differ: {checks}')
    return checks


def build(spark, source: Path, output: Path, reports: Path, protocol_path: Path, binding_path: Path):
    """Persist features and label metadata separately, then report all cohort exclusions and class support."""
    protocol = load_protocol(protocol_path)
    binding = json.loads(binding_path.read_text())
    require(binding['protocol_sha256'] == PROTOCOL_SHA256, 'input protocol binding differs')
    require(binding['source_manifest_sha256'] == protocol['source']['manifest_sha256'], 'input source differs from frozen protocol')
    require(not output.exists(), 'choose a new feature output; partial outputs cannot resume')
    output.mkdir(parents=True)
    started = time.monotonic()

    def progress(phase: str):
        """Expose actual completed/current phases without inventing an overall percentage."""
        save(reports / 'progress.json', {'phase': phase, 'updated_utc': datetime.now(timezone.utc).isoformat(),
                                       'elapsed_seconds': time.monotonic() - started})

    progress('cohort_known_answers')
    known = cohort_checks(spark, protocol)
    readings = spark.read.parquet(str(source / 'silver/stg_telemetry'))
    machines = spark.read.parquet(str(source / 'bronze/raw_machines'))
    failures = spark.read.parquet(str(source / 'bronze/raw_failures'))
    maintenance = spark.read.parquet(str(source / 'bronze/raw_maintenance'))
    require(machines.count() == protocol['source']['machine_count'], 'machine count differs')
    require(machines.groupBy('machine_id').count().filter('count != 1').limit(1).count() == 0, 'initial machine grain differs')
    require(failures.groupBy('machine_id', 'failure_start_ts').count().filter('count != 1').limit(1).count() == 0,
            'ambiguous failure episode timestamps')
    config = protocol['chronological_split']
    start, end = parse_ts(config['start_inclusive']), parse_ts(config['observation_end_exclusive'])
    expected = protocol['source']['machine_count'] * int((end - start).total_seconds()) // protocol['sampling']['snapshot_seconds']
    snapshots = (readings.filter(F.pmod(F.unix_micros('event_ts') - F.lit(int(start.timestamp()) * 1_000_000),
                                      F.lit(protocol['sampling']['snapshot_seconds'] * 1_000_000)) == 0)
                 .select('machine_id', F.col('event_ts').alias('event_timestamp'), 'operating_status').cache())
    require(snapshots.count() == expected, 'snapshot grid count differs')
    require(snapshots.groupBy('machine_id', 'event_timestamp').count().filter('count != 1').limit(1).count() == 0, 'snapshot grain differs')
    require(snapshots.groupBy('machine_id').count().filter(F.col('count') != expected // protocol['source']['machine_count']).limit(1).count() == 0,
            'per-machine snapshot grid count differs')
    bounds = snapshots.agg(F.min('event_timestamp').alias('start'), F.max('event_timestamp').alias('last')).first()
    require(bounds.start == start.replace(tzinfo=None)
            and bounds.last == (end - timedelta(seconds=protocol['sampling']['snapshot_seconds'])).replace(tzinfo=None), 'snapshot bounds differ')

    progress('feature_write')
    features = (compute_features(snapshots.select('machine_id', 'event_timestamp'), readings, machines, failures)
                .withColumn('created', F.lit(datetime.now(timezone.utc))).cache())
    feature_path = output / 'features/feat_machine_10m'
    write_parquet(features, feature_path, 128)
    back = spark.read.parquet(str(feature_path))
    exact_rows_equal(features, back, features.columns)
    require(back.count() == expected and back.filter('max_received_ts > event_timestamp OR max_feature_event_ts > event_timestamp OR created IS NULL').limit(1).count() == 0,
            'feature availability/grain differs')
    require(not ({'operating_status', 'next_failure_ts', 'failure_in_next_1h', 'label_status'} & set(back.columns)), 'truth leaked into feature table')
    features.unpersist()
    progress('label_write')
    labels = label_snapshots(snapshots, failures, maintenance, config['observation_end_exclusive'])
    episodes = failures.select('machine_id', F.col('failure_start_ts').alias('next_failure_ts'), F.col('failure_id').alias('failure_episode_id'))
    labels = labels.join(episodes, ['machine_id', 'next_failure_ts'], 'left').cache()
    label_path = output / 'labels/label_failure_1h'
    write_parquet(labels, label_path, 128)
    labels_back = spark.read.parquet(str(label_path))
    exact_rows_equal(labels, labels_back, labels.columns)
    require(labels_back.count() == expected, 'label count differs')
    require(labels_back.filter("(label_status != 'eligible' AND failure_in_next_1h IS NOT NULL) OR "
                               "(label_status = 'eligible' AND failure_in_next_1h IS NULL) OR "
                               "(failure_in_next_1h = 1 AND (failure_episode_id IS NULL OR next_failure_ts <= event_timestamp OR "
                               "next_failure_ts > label_end_ts OR operating_status != 'operating'))").limit(1).count() == 0,
            'label availability/status/episode contract differs')
    labels.unpersist()
    snapshots.unpersist()
    progress('cohort_and_class_support')
    audit = cohort(back.join(labels_back, ['machine_id', 'event_timestamp']), protocol).cache()
    reasons = {r.cohort_reason: r['count'] for r in audit.groupBy('cohort_reason').count().collect()}
    require(sum(reasons.values()) == expected, 'exclusion accounting does not cover every snapshot')
    label_statuses = {r.label_status: r['count'] for r in audit.groupBy('label_status').count().collect()}
    included = audit.filter("cohort_reason = 'included'")
    split_rows = included.groupBy('split').agg(F.count('*').alias('rows'),
        F.sum('failure_in_next_1h').alias('positive_rows'),
        F.sum(F.when(F.col('failure_in_next_1h') == 0, 1).otherwise(0)).alias('negative_rows'),
        F.countDistinct(F.when(F.col('failure_in_next_1h') == 1, F.col('failure_episode_id'))).alias('failure_episodes'),
        F.min('event_timestamp').alias('first_event'), F.max('event_timestamp').alias('last_event'),
        F.max('label_available_ts').alias('last_label_available')).collect()
    splits = {r.split: {k: v for k, v in r.asDict().items() if k != 'split'} for r in split_rows}
    support = class_support(splits, protocol)
    require(included.filter("(split = 'train' AND label_available_ts >= TIMESTAMP '" + config['train_end_exclusive'] + "') OR "
                            "(split = 'validation' AND label_available_ts >= TIMESTAMP '" + config['validation_end_exclusive'] + "')").limit(1).count() == 0,
            'label boundary leakage')
    write_parquet(audit.select('machine_id', 'event_timestamp', 'label_status', 'cohort_reason', 'split'), output / 'audit/cohort', 32)
    # Future training must still retrieve the eight feature columns via Feast; this table is the label/cohort contract.
    write_parquet(included.select('machine_id', 'event_timestamp', 'failure_in_next_1h', 'label_available_ts', 'failure_episode_id', 'split'),
                  output / 'training/eligible_entities', 32)
    audit.unpersist()
    progress('storage_and_output_binding')
    paths = ['features/feat_machine_10m', 'labels/label_failure_1h', 'audit/cohort', 'training/eligible_entities']
    storage = {name: codec_inventory(spark, output / name) for name in paths}
    report = {'passed': True, 'known_answer_checks': known, 'source_manifest_sha256': binding['source_manifest_sha256'],
        'input_binding_sha256': digest(binding_path), 'protocol_sha256': PROTOCOL_SHA256, 'snapshot_rows': expected,
        'label_status_counts': label_statuses, 'cohort_exclusion_counts': reasons, 'splits': splits,
        'class_support': support, 'storage': storage, 'files': inventory(output),
        'code_sha256': {str(p.relative_to(ROOT)): digest(p)
                        for folder in ('generator', 'pipelines', 'ml') for p in sorted((ROOT / folder).glob('*.py'))},
        'application_id': spark.sparkContext.applicationId, 'spark': spark.version, 'python': platform.python_version(),
        'execution': {'master': spark.sparkContext.master, 'shuffle_partitions': spark.conf.get('spark.sql.shuffle.partitions'),
                      'aqe_coalesce_partitions': spark.conf.get('spark.sql.adaptive.coalescePartitions.enabled'),
                      'timezone': spark.conf.get('spark.sql.session.timeZone'),
                      'driver_memory': spark.sparkContext.getConf().get('spark.driver.memory'),
                      'scratch': spark.sparkContext.getConf().get('spark.local.dir')},
        'elapsed_seconds': time.monotonic() - started, 'completed_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'Frozen 60-day source feature/label preparation and class support only. No training metrics, Feast, KFP or full100GB feature proof.'}
    save_json(reports / 'feature-report.json', report)
    save(output / '_FEATURES_SUCCESS.json', {'report_sha256': digest(reports / 'feature-report.json'), 'protocol_sha256': PROTOCOL_SHA256})
    progress('completed')
    print(json.dumps({'passed': True, 'snapshot_rows': expected, 'class_support': support, 'cohort_counts': reasons}), flush=True)


def main():
    """Read only explicit typed input paths and preserve failed/partial outputs for inspection."""
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('input', 'output', 'reports', 'protocol', 'binding'):
        parser.add_argument('--' + arg, type=Path, required=True)
    args = parser.parse_args()
    spark = SparkSession.builder.appName('iot-medium-60d-features').getOrCreate()
    spark.conf.set('spark.sql.session.timeZone', 'UTC')
    try:
        build(spark, args.input, args.output, args.reports, args.protocol, args.binding)
    finally:
        spark.stop()


if __name__ == '__main__':
    main()
