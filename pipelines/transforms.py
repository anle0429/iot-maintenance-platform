"""Parse source JSON, deduplicate measurements and build temporal dimensions using Spark."""

from pyspark.sql import DataFrame, Window, functions as F, types as T


def struct_schema(fields: list[tuple[str, str]]) -> T.StructType:
    """Construct explicit nullable source types instead of inferring schema from one file."""
    types = {"string": T.StringType(), "timestamp": T.TimestampType(), "double": T.DoubleType(),
             "int": T.IntegerType(), "boolean": T.BooleanType()}
    return T.StructType([T.StructField(name, types[kind], True) for name, kind in fields])


TELEMETRY_FIELDS = [(name, "string") for name in ("event_id", "machine_id", "operating_status")]
TELEMETRY_FIELDS += [(name, "timestamp") for name in ("event_ts", "received_ts", "created_ts")]
TELEMETRY_FIELDS += [(name, "double") for name in ("temperature_c", "vibration_rms_mm_s", "current_a", "rpm", "pressure_bar")]
TELEMETRY_FIELDS += [("schema_version", "int")]
MACHINE_FIELDS = [(name, "string") for name in ("machine_id", "factory_id", "machine_type")]
MACHINE_FIELDS += [(name, "timestamp") for name in ("installed_at", "valid_from_ts", "recorded_ts")]
MACHINE_FIELDS += [("rated_rpm", "int")]
FAILURE_FIELDS = [(name, "string") for name in ("failure_id", "machine_id", "failure_type")]
FAILURE_FIELDS += [(name, "timestamp") for name in ("failure_start_ts", "recovered_ts", "recorded_ts")]
MAINTENANCE_FIELDS = [(name, "string") for name in ("maintenance_id", "machine_id", "maintenance_type", "component")]
MAINTENANCE_FIELDS += [(name, "timestamp") for name in ("start_ts", "end_ts", "created_ts")]


def require(condition: bool, message: str) -> None:
    """Stop the job on a failed data invariant, even when Python assertions are disabled."""
    if not condition:
        raise ValueError(message)


def parse_records(raw: DataFrame, fields: list[tuple[str, str]]) -> DataFrame:
    """Parse one source table while retaining the original source file and line number."""
    return raw.select("source_path", "source_line", F.from_json("payload", struct_schema(fields), {"mode": "FAILFAST"}).alias("data")).select("data.*", "source_path", "source_line")


def deduplicate_telemetry(frame: DataFrame) -> DataFrame:
    """Reject conflicting duplicates, then retain the latest export of each immutable reading."""
    payload_columns = [name for name, _ in TELEMETRY_FIELDS if name != "created_ts"]
    hashes = frame.withColumn("payload_hash", F.sha2(F.to_json(F.struct(*payload_columns)), 256))
    conflicts = hashes.groupBy("event_id").agg(F.countDistinct("payload_hash").alias("variants")).filter("variants > 1")
    require(conflicts.limit(1).count() == 0, "conflicting duplicate telemetry payload")
    window = Window.partitionBy("event_id").orderBy(F.col("created_ts").desc(), F.col("source_path"), F.col("source_line").desc())
    return frame.withColumn("rank", F.row_number().over(window)).filter("rank = 1").drop("rank")


def build_dim_machine(snapshots: DataFrame, observation_start: str) -> DataFrame:
    """Close source snapshots into SCD2 intervals without erasing their recorded/availability time."""
    duplicates = snapshots.groupBy("machine_id", "valid_from_ts").count().filter("count > 1")
    require(duplicates.limit(1).count() == 0, "duplicate machine snapshot effective time")
    ordered = Window.partitionBy("machine_id").orderBy("valid_from_ts")
    dimension = (snapshots.drop("source_path", "source_line")
                 .withColumn("recorded_ts", F.coalesce("recorded_ts", F.lit(observation_start).cast("timestamp")))
                 .withColumn("valid_to_ts", F.lead("valid_from_ts").over(ordered))
                 .withColumn("is_current", F.col("valid_to_ts").isNull())
                 .withColumn("machine_sk", F.sha2(F.concat_ws("|", "machine_id", F.col("valid_from_ts").cast("string")), 256)))
    require(dimension.filter("valid_from_ts IS NULL OR machine_id IS NULL OR factory_id IS NULL OR recorded_ts < valid_from_ts OR valid_to_ts <= valid_from_ts").limit(1).count() == 0,
            "invalid machine dimension interval or availability")
    require(dimension.groupBy("machine_id").agg(F.sum(F.col("is_current").cast("int")).alias("current_count")).filter("current_count != 1").limit(1).count() == 0,
            "machine must have exactly one current version")
    return dimension


def temporal_fact(events: DataFrame, dimension: DataFrame, timestamp_column: str) -> DataFrame:
    """Attach the version effective at event time; this analytics join alone is not a feature PIT proof."""
    event, dim = events.alias("e"), dimension.alias("d")
    condition = ((F.col("e.machine_id") == F.col("d.machine_id"))
                 & (F.col(f"e.{timestamp_column}") >= F.col("d.valid_from_ts"))
                 & (F.col("d.valid_to_ts").isNull() | (F.col(f"e.{timestamp_column}") < F.col("d.valid_to_ts"))))
    return event.join(dim, condition, "left").select("e.*", "d.machine_sk", "d.factory_id")
