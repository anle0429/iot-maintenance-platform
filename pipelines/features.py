"""Build event-and-arrival-aware features, censored labels and purged chronological splits."""

from pyspark.sql import DataFrame, functions as F


FEATURE_COLUMNS = ["temperature_mean_10m", "vibration_max_10m", "current_mean_10m", "current_std_10m",
                   "rpm_mean_10m", "reading_count_10m", "machine_age_days", "prior_failures_24h"]


def label_snapshots(snapshots: DataFrame, failures: DataFrame, maintenance: DataFrame, observation_end: str) -> DataFrame:
    """Use future truth only for labels/censoring; never expose these fields as model features."""
    spark = snapshots.sparkSession
    snapshots.createOrReplaceTempView("label_snapshots_input")
    failures.createOrReplaceTempView("label_failures_input")
    maintenance.createOrReplaceTempView("label_maintenance_input")
    future = spark.sql("""
        SELECT s.machine_id, s.event_timestamp,
               MIN(f.failure_start_ts) AS next_failure_ts
        FROM label_snapshots_input s LEFT JOIN label_failures_input f
          ON s.machine_id=f.machine_id AND f.failure_start_ts>s.event_timestamp
         AND f.failure_start_ts<=s.event_timestamp+INTERVAL 1 HOUR
        GROUP BY s.machine_id,s.event_timestamp
    """)
    intervention = spark.sql("""
        SELECT s.machine_id,s.event_timestamp,COUNT(m.maintenance_id) AS interventions
        FROM label_snapshots_input s LEFT JOIN label_maintenance_input m
          ON s.machine_id=m.machine_id AND m.start_ts>s.event_timestamp
         AND m.start_ts<=s.event_timestamp+INTERVAL 1 HOUR
        GROUP BY s.machine_id,s.event_timestamp
    """)
    result = snapshots.join(future, ["machine_id", "event_timestamp"]).join(intervention, ["machine_id", "event_timestamp"])
    result = result.withColumn("label_end_ts", F.expr("event_timestamp + INTERVAL 1 HOUR"))
    result = result.withColumn("label_available_ts", F.expr("label_end_ts + INTERVAL 30 SECONDS"))
    result = result.withColumn("label_status", F.when(F.col("operating_status") != "operating", "stopped")
                               .when(F.col("label_end_ts") >= F.lit(observation_end).cast("timestamp"), "censored")
                               .when(F.col("interventions") > 0, "maintenance").otherwise("eligible"))
    return result.withColumn("failure_in_next_1h", F.when(F.col("label_status") == "eligible", F.col("next_failure_ts").isNotNull().cast("int")))


def compute_features(snapshots: DataFrame, telemetry: DataFrame, initial_machines: DataFrame, failures: DataFrame) -> DataFrame:
    """Aggregate only arrived past readings and already recorded failures at each snapshot time."""
    spark = snapshots.sparkSession
    snapshots.createOrReplaceTempView("feature_snapshots_input")
    telemetry.createOrReplaceTempView("feature_telemetry_input")
    initial_machines.createOrReplaceTempView("feature_initial_machines_input")
    failures.createOrReplaceTempView("feature_failures_input")
    rolling = spark.sql("""
        SELECT s.machine_id,s.event_timestamp,
               AVG(t.temperature_c) AS temperature_mean_10m,
               MAX(t.vibration_rms_mm_s) AS vibration_max_10m,
               AVG(t.current_a) AS current_mean_10m,
               COALESCE(STDDEV_POP(t.current_a),0D) AS current_std_10m,
               AVG(t.rpm) AS rpm_mean_10m,COUNT(t.event_id) AS reading_count_10m,
               MAX(t.received_ts) AS max_received_ts, MAX(t.event_ts) AS max_feature_event_ts
        FROM feature_snapshots_input s LEFT JOIN feature_telemetry_input t
          ON s.machine_id=t.machine_id
         AND t.event_ts>s.event_timestamp-INTERVAL 10 MINUTES AND t.event_ts<=s.event_timestamp
         AND t.received_ts<=s.event_timestamp
        GROUP BY s.machine_id,s.event_timestamp
    """)
    history = spark.sql("""
        SELECT s.machine_id,s.event_timestamp,COUNT(f.failure_id) AS prior_failures_24h
        FROM feature_snapshots_input s LEFT JOIN feature_failures_input f
          ON s.machine_id=f.machine_id AND f.failure_start_ts<=s.event_timestamp
         AND f.recorded_ts<=s.event_timestamp
         AND f.failure_start_ts>s.event_timestamp-INTERVAL 24 HOURS
        GROUP BY s.machine_id,s.event_timestamp
    """)
    ages = spark.sql("""
        SELECT s.machine_id,s.event_timestamp,
               (unix_timestamp(s.event_timestamp)-unix_timestamp(m.installed_at))/86400D AS machine_age_days
        FROM feature_snapshots_input s JOIN feature_initial_machines_input m
          ON s.machine_id=m.machine_id AND m.valid_from_ts<=s.event_timestamp
    """)
    return (rolling.join(history, ["machine_id", "event_timestamp"])
            .join(ages, ["machine_id", "event_timestamp"]).withColumn("created", F.current_timestamp()))


def with_temporal_split(frame: DataFrame, train_end: str, validation_end: str) -> DataFrame:
    """Purge overlapping/unavailable labels before validation/test boundaries."""
    train = F.lit(train_end).cast("timestamp")
    validation = F.lit(validation_end).cast("timestamp")
    return frame.withColumn("split", F.when((F.col("event_timestamp") < train) & (F.col("label_available_ts") < train), "train")
                            .when((F.col("event_timestamp") >= train) & (F.col("event_timestamp") < validation)
                                  & (F.col("label_available_ts") < validation), "validation")
                            .when(F.col("event_timestamp") >= validation, "test"))
