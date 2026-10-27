"""Bound temporal join candidates while retaining exact event/arrival and label rules."""

from pyspark.sql import DataFrame, functions as F


def _bucket(column: str, seconds: int):
    """Use UTC epoch microseconds and floor, including timestamps before the epoch."""
    return F.floor(F.unix_micros(column) / F.lit(seconds * 1_000_000)).cast("long")


def _candidates(snapshots: DataFrame, events: DataFrame, event_column: str,
                seconds: int, direction: int, predicate: str) -> DataFrame:
    """Left join two neighboring buckets, then enforce the original exact interval."""
    if direction not in (-1, 1) or seconds <= 0:
        raise ValueError("A positive window and past/future direction are required")
    left = (snapshots.select("machine_id", "event_timestamp")
            .withColumn("_bucket", _bucket("event_timestamp", seconds))
            .withColumn("_bucket", F.explode(F.array(F.col("_bucket"), F.col("_bucket") + direction)))
            .alias("s"))
    right = events.withColumn("_bucket", _bucket(event_column, seconds)).alias("e")
    return left.join(right, (F.col("s.machine_id") == F.col("e.machine_id"))
                     & (F.col("s._bucket") == F.col("e._bucket")) & F.expr(predicate), "left")


def _by_snapshot(frame: DataFrame):
    """Group both expanded buckets back to the original machine/time grain."""
    return frame.groupBy(F.col("s.machine_id").alias("machine_id"),
                         F.col("s.event_timestamp").alias("event_timestamp"))


def compute_features(snapshots: DataFrame, telemetry: DataFrame,
                     initial_machines: DataFrame, failures: DataFrame) -> DataFrame:
    """Compute the existing eight features with bounded, arrival-aware past joins."""
    rolling = _by_snapshot(_candidates(snapshots, telemetry, "event_ts", 600, -1,
        "e.event_ts > s.event_timestamp - INTERVAL 10 MINUTES "
        "AND e.event_ts <= s.event_timestamp AND e.received_ts <= s.event_timestamp")).agg(
            F.avg("e.temperature_c").alias("temperature_mean_10m"),
            F.max("e.vibration_rms_mm_s").alias("vibration_max_10m"),
            F.avg("e.current_a").alias("current_mean_10m"),
            F.coalesce(F.stddev_pop("e.current_a"), F.lit(0.0)).alias("current_std_10m"),
            F.avg("e.rpm").alias("rpm_mean_10m"), F.count("e.event_id").alias("reading_count_10m"),
            F.max("e.received_ts").alias("max_received_ts"),
            F.max("e.event_ts").alias("max_feature_event_ts"))
    history = _by_snapshot(_candidates(snapshots, failures, "failure_start_ts", 86400, -1,
        "e.failure_start_ts > s.event_timestamp - INTERVAL 24 HOURS "
        "AND e.failure_start_ts <= s.event_timestamp AND e.recorded_ts <= s.event_timestamp")).agg(
            F.count("e.failure_id").alias("prior_failures_24h"))
    ages = (snapshots.alias("s").join(initial_machines.alias("m"),
            (F.col("s.machine_id") == F.col("m.machine_id"))
            & (F.col("m.valid_from_ts") <= F.col("s.event_timestamp")))
            .select(F.col("s.machine_id").alias("machine_id"), F.col("s.event_timestamp").alias("event_timestamp"),
                ((F.unix_timestamp("s.event_timestamp") - F.unix_timestamp("m.installed_at")) / 86400.0)
                .alias("machine_age_days")))
    return (rolling.join(history, ["machine_id", "event_timestamp"])
            .join(ages, ["machine_id", "event_timestamp"]).withColumn("created", F.current_timestamp()))


def label_snapshots(snapshots: DataFrame, failures: DataFrame,
                    maintenance: DataFrame, observation_end: str) -> DataFrame:
    """Keep future truth in labels/censoring only; exact horizon endpoints are unchanged."""
    future = _by_snapshot(_candidates(snapshots, failures, "failure_start_ts", 3600, 1,
        "e.failure_start_ts > s.event_timestamp "
        "AND e.failure_start_ts <= s.event_timestamp + INTERVAL 1 HOUR")).agg(
            F.min("e.failure_start_ts").alias("next_failure_ts"))
    intervention = _by_snapshot(_candidates(snapshots, maintenance, "start_ts", 3600, 1,
        "e.start_ts > s.event_timestamp AND e.start_ts <= s.event_timestamp + INTERVAL 1 HOUR")).agg(
            F.count("e.maintenance_id").alias("interventions"))
    result = snapshots.join(future, ["machine_id", "event_timestamp"]).join(intervention, ["machine_id", "event_timestamp"])
    result = result.withColumn("label_end_ts", F.expr("event_timestamp + INTERVAL 1 HOUR"))
    result = result.withColumn("label_available_ts", F.expr("label_end_ts + INTERVAL 30 SECONDS"))
    result = result.withColumn("label_status", F.when(F.col("operating_status") != "operating", "stopped")
        .when(F.col("label_end_ts") >= F.lit(observation_end).cast("timestamp"), "censored")
        .when(F.col("interventions") > 0, "maintenance").otherwise("eligible"))
    return result.withColumn("failure_in_next_1h", F.when(F.col("label_status") == "eligible",
        F.col("next_failure_ts").isNotNull().cast("int")))
