"""Read the department database, write validated Parquet zones and train a small chronological baseline."""

import argparse
from datetime import datetime, timedelta
from hashlib import sha256
import json
from math import isclose
import os
from pathlib import Path
import platform
import time

from pyspark.sql import SparkSession, functions as F

from generator.config import Config, iso_ts
from ml.baseline import train_baseline
from pipelines.features import FEATURE_COLUMNS, compute_features, label_snapshots, with_temporal_split
from pipelines.source_loader import validate_run_id
from pipelines.spark_checks import run_spark_checks
from pipelines.transforms import (FAILURE_FIELDS, MACHINE_FIELDS, MAINTENANCE_FIELDS, TELEMETRY_FIELDS,
                                  build_dim_machine, deduplicate_telemetry, parse_records, require, temporal_fact)


def write_report(path: Path, report: dict) -> None:
    """Serialize metrics and UTC trace timestamps without putting passwords into evidence."""
    def encode(value):
        """Format timestamps returned by Spark, whose session timezone is UTC."""
        if isinstance(value, datetime):
            return value.isoformat() + ("Z" if value.tzinfo is None else "")
        raise TypeError(type(value).__name__)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=encode) + "\n", encoding="utf-8")


def jdbc_read(spark: SparkSession, query: str):
    """Read a source query through JDBC, using a local Docker secret rather than source credentials."""
    password = Path("/run/secrets/source_password").read_text().strip()
    return (spark.read.format("jdbc").option("url", "jdbc:postgresql://source:5432/iot_source")
            .option("dbtable", f"({query}) department_source").option("driver", "org.postgresql.Driver")
            .option("user", "iot_ingest").option("password", password).option("fetchsize", 2000).load())


def save_table(frame, output: Path, layer: str, name: str) -> None:
    """Write a new Snappy Parquet table without overwriting another run."""
    frame.write.mode("errorifexists").option("compression", "snappy").parquet(str(output / layer / name))


def run_batch(spark: SparkSession, run_id: str, output: Path) -> dict:
    """Execute the small batch data-to-prediction path with explicit correctness checks."""
    started = time.perf_counter()
    require(not output.exists(), "batch output already exists; choose a new run ID/output directory")
    checks = run_spark_checks(spark)
    print(f"PASS: {len(checks)} Spark known-answer checks", flush=True)
    manifest_rows = jdbc_read(spark, f"SELECT manifest::text AS manifest FROM source_runs WHERE source_run='{run_id}'").collect()
    require(len(manifest_rows) == 1, "source run must exist exactly once")
    source = json.loads(manifest_rows[0].manifest)
    config = Config(**source["original_manifest"]["config"])
    raw = jdbc_read(spark, f"SELECT source_path,source_line,payload::text AS payload FROM source_records WHERE source_run='{run_id}'")
    raw = raw.withColumn("source_run", F.lit(run_id)).withColumn("ingested_at", F.current_timestamp()).cache()
    raw_count = raw.count()
    require(raw_count == source["expected_records"], "Bronze source row count mismatch")
    save_table(raw, output, "bronze", "raw_department_records")
    print(f"Bronze persisted: {raw_count} department records", flush=True)
    write_report(output / "bronze" / "source_manifest.json", source)
    telemetry = parse_records(raw.filter("source_path LIKE 'telemetry/%'"), TELEMETRY_FIELDS).cache()
    failures = parse_records(raw.filter("source_path='failure_events.jsonl'"), FAILURE_FIELDS).cache()
    maintenance = parse_records(raw.filter("source_path='maintenance_events.jsonl'"), MAINTENANCE_FIELDS).cache()
    initial_machines = parse_records(raw.filter("source_path='machines.jsonl'"), MACHINE_FIELDS).cache()
    snapshots = parse_records(raw.filter("source_path IN ('machines.jsonl','machine_changes.jsonl')"), MACHINE_FIELDS)
    silver = deduplicate_telemetry(telemetry).cache()
    silver_count = silver.count()
    require(silver_count == config.unique_rows, "Silver must contain exactly the configured unique readings")
    require(silver.filter("event_id IS NULL OR machine_id IS NULL OR event_ts IS NULL OR received_ts < event_ts OR created_ts < received_ts OR temperature_c IS NULL OR schema_version NOT IN (1,2)").limit(1).count() == 0,
            "telemetry source contract failed")
    require(silver.filter("schema_version=1 AND pressure_bar IS NOT NULL").count() == 0, "v1 pressure must remain unknown")
    require(silver.filter("schema_version=2 AND pressure_bar IS NULL").count() == 0, "v2 pressure must be present")
    save_table(silver, output, "silver", "stg_telemetry")
    save_table(failures, output, "silver", "stg_failure")
    save_table(maintenance, output, "silver", "stg_maintenance")
    save_table(snapshots, output, "silver", "stg_machine_snapshots")
    print(f"Silver persisted: {silver_count} unique readings", flush=True)

    dimension = build_dim_machine(snapshots, iso_ts(config.start)).cache()
    fact = temporal_fact(silver, dimension, "event_ts").cache()
    failure_fact = temporal_fact(failures, dimension, "failure_start_ts")
    maintenance_fact = temporal_fact(maintenance, dimension, "start_ts")
    require(fact.count() == silver_count and fact.filter("machine_sk IS NULL").count() == 0, "telemetry dimension join lost or duplicated rows")
    require(failure_fact.count() == failures.count() and failure_fact.filter("machine_sk IS NULL").count() == 0, "failure dimension FK failed")
    require(maintenance_fact.count() == maintenance.count() and maintenance_fact.filter("machine_sk IS NULL").count() == 0, "maintenance dimension FK failed")
    save_table(dimension, output, "gold", "dim_machine")
    factories = dimension.select("factory_id").distinct()
    save_table(factories, output, "gold", "dim_factory")
    save_table(fact, output, "gold", "fact_telemetry")
    save_table(failure_fact, output, "gold", "fact_failure")
    save_table(maintenance_fact, output, "gold", "fact_maintenance")
    print("Gold dimensions/facts persisted and foreign keys verified", flush=True)

    snapshot_frame = (silver.filter(F.pmod(F.unix_timestamp("event_ts") - int(config.start.timestamp()), F.lit(300)) == 0)
                      .select("machine_id", F.col("event_ts").alias("event_timestamp"), "operating_status").cache())
    labels = label_snapshots(snapshot_frame, failures, maintenance, iso_ts(config.end)).cache()
    features = compute_features(snapshot_frame.select("machine_id", "event_timestamp"), silver, initial_machines, failures).cache()
    feature_count = features.count()
    require(features.filter("max_received_ts > event_timestamp OR max_feature_event_ts > event_timestamp").count() == 0,
            "feature contains a measurement unavailable at prediction time")
    require(features.groupBy("machine_id", "event_timestamp").count().filter("count != 1").count() == 0, "feature grain is not machine/time")
    save_table(features, output, "gold", "feat_machine_10m")
    save_table(labels, output, "gold", "label_failure_1h")
    training = features.join(labels, ["machine_id", "event_timestamp"]).filter("label_status='eligible' AND reading_count_10m >= 5")
    train_end = config.start + (config.end - config.start) * 0.6
    validation_end = config.start + (config.end - config.start) * 0.8
    training = with_temporal_split(training, iso_ts(train_end), iso_ts(validation_end)).cache()
    purged = training.filter("split IS NULL").count()
    training = training.filter("split IS NOT NULL").cache()
    require(training.filter((F.col("split") == "train") & (F.col("label_available_ts") >= F.lit(iso_ts(train_end)).cast("timestamp"))).count() == 0,
            "training label overlaps validation boundary")
    require(training.filter((F.col("split") == "validation") & (F.col("label_available_ts") >= F.lit(iso_ts(validation_end)).cast("timestamp"))).count() == 0,
            "validation label overlaps test boundary")
    save_table(training, output, "gold", "training_failure_1h")
    print("Features/labels persisted; event/arrival and split boundaries verified", flush=True)
    model_report, predictions = train_baseline(training, output / "ml")
    example = predictions.filter("machine_id='P-001'").orderBy("event_timestamp").first()
    if example is None:
        example = predictions.orderBy("event_timestamp", "machine_id").first()
    selected_feature = features.filter((F.col("machine_id") == example.machine_id) & (F.col("event_timestamp") == example.event_timestamp)).first()
    contributing = fact.filter((F.col("machine_id") == example.machine_id)
                               & (F.col("event_ts") > F.lit(example.event_timestamp - timedelta(minutes=10)))
                               & (F.col("event_ts") <= F.lit(example.event_timestamp))
                               & (F.col("received_ts") <= F.lit(example.event_timestamp)))
    feature_inputs = contributing.orderBy("event_ts").collect()
    require(len(feature_inputs) == selected_feature.reading_count_10m and len(feature_inputs) > 0,
            "feature trace must contain exactly the arrived window readings")
    require(isclose(sum(row.temperature_c for row in feature_inputs) / len(feature_inputs),
                    selected_feature.temperature_mean_10m, abs_tol=1e-9), "source readings do not reproduce feature mean")
    provenance = raw.join(contributing.select("source_path", "source_line"), ["source_path", "source_line"]).collect()
    original_by_key = {(row.source_path, row.source_line): json.loads(row.payload) for row in provenance}
    require(len(original_by_key) == len(feature_inputs), "feature input provenance is incomplete")
    require(all(original_by_key[(row.source_path, row.source_line)]["event_id"] == row.event_id
                and original_by_key[(row.source_path, row.source_line)]["temperature_c"] == row.temperature_c
                for row in feature_inputs), "feature input differs from its Bronze source payload")
    require(all(isclose(example[name], selected_feature[name], abs_tol=1e-9) for name in FEATURE_COLUMNS),
            "prediction vector differs from the traced feature row")
    selected_event = feature_inputs[-1]
    readback = spark.read.parquet(str(output / "silver" / "stg_telemetry"))
    require(readback.count() == silver_count and readback.select("event_id").distinct().count() == silver_count,
            "persisted Silver differs from validated reading grain")
    checks.update({"bronze_source_count": True, "silver_count_and_schema": True, "fact_dimension_foreign_keys": True,
                   "feature_event_and_arrival_time": True, "feature_grain": True, "split_label_purge": True,
                   "persisted_silver_readback": True, "source_to_prediction_trace": True})
    code_hashes = [{"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}
                   for folder in ("generator", "pipelines", "ml", "tests") for path in sorted(Path(folder).rglob("*.py"))]
    parquet_files = list(output.rglob("*.parquet"))
    return {"passed": True, "checks": checks, "source_run": run_id,
            "runtime": {"spark": spark.version, "java": spark.sparkContext._jvm.java.lang.System.getProperty("java.version"),
                        "python": platform.python_version(), "master": spark.sparkContext.master,
                        "base_commit": os.environ.get("IOT_BASE_COMMIT", "UNCOMMITTED"), "source_sha256": code_hashes},
            "counts": {"bronze_records": raw_count, "bronze_telemetry": telemetry.count(), "silver_telemetry": silver_count,
                       "deduplicated_extras": telemetry.count() - silver_count, "dim_machine_versions": dimension.count(),
                       "dim_machine_current": dimension.filter("is_current").count(), "factories": factories.count(),
                       "failures": failures.count(), "maintenance": maintenance.count(), "feature_rows": feature_count,
                       "purged_boundary_rows": purged, "training_rows": training.count()},
            "label_status_counts": {r.label_status: r["count"] for r in labels.groupBy("label_status").count().collect()},
            "schema_unique_counts": {str(r.schema_version): r["count"] for r in silver.groupBy("schema_version").count().collect()},
            "schemas": {"bronze.raw_department_records": raw.schema.jsonValue(), "silver.stg_telemetry": silver.schema.jsonValue(),
                        "gold.dim_machine": dimension.schema.jsonValue(), "gold.fact_telemetry": fact.schema.jsonValue(),
                        "gold.feat_machine_10m": features.schema.jsonValue(), "gold.label_failure_1h": labels.schema.jsonValue(),
                        "gold.training_failure_1h": training.schema.jsonValue()},
            "machine_history_example": [r.asDict() for r in dimension.filter("machine_id='P-001'").orderBy("valid_from_ts").collect()],
            "split_boundaries": {"train_end_exclusive": iso_ts(train_end), "validation_end_exclusive": iso_ts(validation_end),
                                 "purge_rule": "label_available_ts < next split start; availability includes 30s recording delay"},
            "baseline": model_report, "example_prediction": example.asDict(),
            "example_source_event": selected_event.asDict(), "example_feature": selected_feature.asDict(),
            "example_feature_inputs": [row.asDict() for row in feature_inputs],
            "parquet_file_count": len(parquet_files), "parquet_bytes": sum(path.stat().st_size for path in parquet_files),
            "elapsed_seconds": time.perf_counter() - started,
            "scope": "single small local[2] baseline run; no optimization/100GB/cluster-serving claim"}


def main() -> None:
    """Run the batch milestone and write evidence only after all validations succeed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=validate_run_id)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    spark = SparkSession.builder.appName(f"iot-batch-{args.run_id}").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    try:
        report = run_batch(spark, args.run_id, args.output)
        write_report(args.report, report)
        write_report(args.output / "_RUN_SUCCESS.json", {"passed": True, "source_run": args.run_id, "report": str(args.report), "counts": report["counts"]})
        print(f"PASS: {len(report['checks'])} checks, {report['counts']['silver_telemetry']} readings, baseline prediction saved; report={args.report}")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
