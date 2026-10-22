"""Check Spark transformations against tiny, independently specified answers before batch execution."""

from datetime import timedelta
import json

from generator.config import parse_ts
from pipelines.features import compute_features, label_snapshots, with_temporal_split
from pipelines.temporal import label_snapshot, temporal_split
from pipelines.transforms import (MACHINE_FIELDS, TELEMETRY_FIELDS, build_dim_machine,
                                  deduplicate_telemetry, parse_records, require, temporal_fact)


def run_spark_checks(spark) -> dict:
    """Exercise schema, dedup, SCD2, horizon, arrival and split boundaries with known outcomes."""
    checks = {}
    origin = parse_ts("2026-10-01T00:00:00Z")

    def raw(rows):
        """Construct source-like JSON strings so parsing itself participates in the fixture."""
        return spark.createDataFrame([("fixture.jsonl", index + 1, json.dumps(row)) for index, row in enumerate(rows)],
                                     "source_path string, source_line long, payload string")

    base = {"event_id": "A", "machine_id": "P", "operating_status": "operating",
            "event_ts": "2026-10-01T00:00:00Z", "received_ts": "2026-10-01T00:00:01Z",
            "created_ts": "2026-10-01T00:00:01Z", "temperature_c": 50, "vibration_rms_mm_s": 2,
            "current_a": 10, "rpm": 2900, "schema_version": 1}
    newer = {**base, "created_ts": "2026-10-01T01:00:01Z"}
    v2 = {**base, "event_id": "B", "event_ts": "2026-10-01T00:01:00Z",
          "received_ts": "2026-10-01T00:01:01Z", "created_ts": "2026-10-01T00:01:01Z",
          "schema_version": 2, "pressure_bar": 4.5}
    parsed = parse_records(raw([base, newer, v2]), TELEMETRY_FIELDS)
    dedup = deduplicate_telemetry(parsed).cache()
    rows = {row.event_id: row for row in dedup.collect()}
    checks["dedup_keeps_latest_export"] = len(rows) == 2 and rows["A"].created_ts.hour == 1
    checks["schema_union_preserves_unknown_pressure"] = rows["A"].pressure_bar is None and rows["B"].pressure_bar == 4.5
    checks["offline_replay_idempotent"] = deduplicate_telemetry(parsed.unionByName(parsed)).count() == 2
    try:
        deduplicate_telemetry(parse_records(raw([base, {**newer, "temperature_c": 999}]), TELEMETRY_FIELDS))
    except ValueError:
        checks["conflicting_duplicate_rejected"] = True
    else:
        checks["conflicting_duplicate_rejected"] = False

    initial = {"machine_id": "P", "factory_id": "F-01", "machine_type": "pump", "rated_rpm": 3000,
               "installed_at": "2026-09-01T00:00:00Z", "valid_from_ts": "2026-10-01T00:00:00Z"}
    changed = {**initial, "factory_id": "F-02", "valid_from_ts": "2026-10-01T01:00:00Z", "recorded_ts": "2026-10-01T01:05:00Z"}
    machine_rows = parse_records(raw([initial, changed]), MACHINE_FIELDS)
    dim = build_dim_machine(machine_rows, "2026-10-01T00:00:00Z")
    events = spark.createDataFrame([("P", origin + timedelta(seconds=3599)), ("P", origin + timedelta(hours=1))],
                                   "machine_id string, event_ts timestamp")
    joined = temporal_fact(events, dim, "event_ts").orderBy("event_ts").collect()
    checks["scd2_half_open_boundary"] = [r.factory_id for r in joined] == ["F-01", "F-02"]
    checks["scd2_one_current"] = dim.filter("is_current").count() == 1 and dim.count() == 2
    try:
        build_dim_machine(machine_rows.unionByName(machine_rows), "2026-10-01T00:00:00Z")
    except ValueError:
        checks["duplicate_effective_time_rejected"] = True
    else:
        checks["duplicate_effective_time_rejected"] = False

    snapshots = [("edge", origin, "operating"), ("outside", origin, "operating"),
                 ("stopped", origin, "failed"), ("censored", origin + timedelta(hours=3), "operating"),
                 ("intervened", origin, "operating"), ("normal", origin, "operating")]
    failure_rows = [("F1", "edge", origin + timedelta(hours=1), origin + timedelta(hours=1, seconds=30)),
                    ("F2", "outside", origin + timedelta(seconds=3601), origin + timedelta(seconds=3631)),
                    ("F3", "stopped", origin, origin + timedelta(seconds=30))]
    failure_frame = spark.createDataFrame(failure_rows, "failure_id string, machine_id string, failure_start_ts timestamp, recorded_ts timestamp")
    maintenance_rows = [("M1", "intervened", origin + timedelta(minutes=30))]
    maintenance_frame = spark.createDataFrame(maintenance_rows, "maintenance_id string, machine_id string, start_ts timestamp")
    snapshot_frame = spark.createDataFrame(snapshots, "machine_id string, event_timestamp timestamp, operating_status string")
    labels = {r.machine_id: (r.failure_in_next_1h, r.label_status) for r in label_snapshots(snapshot_frame, failure_frame, maintenance_frame, "2026-10-01T04:00:00Z").collect()}
    oracle_failures = [{"machine_id": r[1], "failure_start_ts": r[2]} for r in failure_rows]
    oracle_maintenance = [{"machine_id": r[1], "start_ts": r[2]} for r in maintenance_rows]
    checks["label_boundaries_match_oracle"] = all(labels[m] == label_snapshot(m, at, status, oracle_failures, oracle_maintenance, origin + timedelta(hours=4)) for m, at, status in snapshots)

    at = origin + timedelta(hours=1)
    readings = [("old", "P", at - timedelta(minutes=10), at - timedelta(minutes=10), 100.0),
                ("known", "P", at - timedelta(minutes=9), at - timedelta(minutes=9), 10.0),
                ("late", "P", at - timedelta(minutes=1), at + timedelta(minutes=2), 1000.0),
                ("now", "P", at, at, 20.0), ("future", "P", at + timedelta(minutes=1), at + timedelta(minutes=1), 999.0)]
    telemetry = spark.createDataFrame(readings, "event_id string, machine_id string, event_ts timestamp, received_ts timestamp, temperature_c double")
    for name in ("vibration_rms_mm_s", "current_a", "rpm"):
        telemetry = telemetry.withColumn(name, telemetry.temperature_c)
    machines = spark.createDataFrame([("P", origin - timedelta(days=30), origin)], "machine_id string, installed_at timestamp, valid_from_ts timestamp")
    old_failures = spark.createDataFrame([("past", "P", origin + timedelta(minutes=10), origin + timedelta(minutes=11)),
                                         ("unknown", "P", origin + timedelta(minutes=20), at + timedelta(minutes=1))],
                                        "failure_id string, machine_id string, failure_start_ts timestamp, recorded_ts timestamp")
    one_snapshot = spark.createDataFrame([("P", at)], "machine_id string, event_timestamp timestamp")
    feature = compute_features(one_snapshot, telemetry, machines, old_failures).first()
    checks["arrival_and_window_boundary"] = feature.temperature_mean_10m == 15 and feature.reading_count_10m == 2
    checks["historical_failure_availability"] = feature.prior_failures_24h == 1

    train_end, validation_end = origin + timedelta(hours=2), origin + timedelta(hours=3)
    split_rows = [(origin, train_end - timedelta(seconds=1)),
                  (origin, train_end), (train_end, validation_end - timedelta(seconds=1)),
                  (train_end, validation_end), (validation_end, validation_end + timedelta(hours=1))]
    split_frame = spark.createDataFrame(split_rows, "event_timestamp timestamp, label_available_ts timestamp")
    actual = with_temporal_split(split_frame, "2026-10-01T02:00:00Z", "2026-10-01T03:00:00Z").collect()
    checks["purged_split_boundaries"] = [r.split for r in actual] == [temporal_split(at, available, train_end, validation_end) for at, available in split_rows]
    dedup.unpersist()
    require(all(checks.values()), f"Spark known-answer checks failed: {checks}")
    return checks
