"""Consume bounded Kafka offsets with real Flink watermarks, keyed deduplication and event-time windows."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import platform
import time

from pyflink.common import Configuration, Duration, Time, Types, WatermarkStrategy
from pyflink.common.serialization import SimpleStringSchema
from pyflink.common.restart_strategy import RestartStrategies
from pyflink.common.watermark_strategy import TimestampAssigner
from pyflink.datastream import StreamExecutionEnvironment, OutputTag
from pyflink.datastream.connectors.kafka import KafkaSource, KafkaOffsetsInitializer, KafkaTopicPartition
from pyflink.datastream.functions import AggregateFunction, KeyedProcessFunction, MapFunction, ProcessWindowFunction
from pyflink.datastream.state import ValueStateDescriptor
from pyflink.datastream.window import TumblingEventTimeWindows

from pipelines.stream_contract import EventLedger, StreamStore, export_sqlite, millis, utc_millis, volume_store_path


AUDIT = OutputTag("audit", Types.STRING())
LATE = OutputTag("too-late", Types.STRING())


class EventTimestamp(TimestampAssigner):
    """Assign device event time, including explicit test progress markers."""

    def extract_timestamp(self, value, record_timestamp):
        """Read event timestamps without replacing them with Kafka ingestion time."""
        row = json.loads(value)
        return millis(row["event"]["event_ts"] if row["kind"] == "telemetry" else row["event_ts"])


class Deduplicate(KeyedProcessFunction):
    """Keep immutable reading payloads for seven processing-time days and audit duplicates/conflicts."""

    def __init__(self, enabled: bool, retention_ms: int, ledger_path: str):
        """Allow the same workload to expose the behavior of a baseline without deduplication."""
        self.enabled = enabled
        self.retention_ms = retention_ms
        self.ledger_path = ledger_path

    def open(self, runtime_context):
        """Configure finite cache state and the separate persistent event journal."""
        self.seen = runtime_context.get_state(ValueStateDescriptor("immutable-event", Types.STRING()))
        self.marker = runtime_context.get_state(ValueStateDescriptor("marker", Types.STRING()))
        self.ledger = EventLedger(Path(self.ledger_path)) if self.enabled else None

    def process_element(self, value, ctx):
        """Drop identical retries before windows and fail visibly on conflicting event payloads."""
        row = json.loads(value)
        if row["kind"] == "progress":
            self.marker.update(value)
            ctx.timer_service().register_processing_time_timer(ctx.timer_service().current_processing_time() + 1000)
            return
        canonical = json.dumps(row["event"], sort_keys=True)
        stored = self.seen.value() if self.enabled else None
        old = json.loads(stored) if stored else None
        now = ctx.timer_service().current_processing_time()
        if old is not None and now < old["expires_at"]:
            if old["payload"] != canonical:
                raise ValueError(f"conflicting event_id: {row['event']['event_id']}")
            yield AUDIT, json.dumps({**row, "kind": "duplicate", "dedup_source":"flink_state"})
            return
        fresh = self.ledger.observe(row['event']) if self.enabled else True
        if self.enabled:
            expires_at = now + self.retention_ms
            self.seen.update(json.dumps({"payload":canonical,"expires_at":expires_at}))
            ctx.timer_service().register_processing_time_timer(expires_at)
        if not fresh:
            yield AUDIT, json.dumps({**row, "kind":"duplicate", "dedup_source":"durable_ledger"})
            return
        yield value
        yield AUDIT, json.dumps({**row, "kind": "unique" if self.enabled else "delivery"})

    def on_timer(self, timestamp, ctx):
        """Expose a real observed Flink watermark as a producer barrier, not an assumed sleep."""
        marker = self.marker.value()
        if marker:
            row = json.loads(marker)
            yield AUDIT, json.dumps({**row, "watermark_ms": ctx.timer_service().current_watermark()})
            self.marker.clear()
            return
        stored = self.seen.value()
        old = json.loads(stored) if stored else None
        if old is not None and timestamp >= old["expires_at"]:
            self.ledger.flush()
            self.seen.clear()
            yield AUDIT, json.dumps({"kind":"expired","event_id":ctx.get_current_key(),
                                    "sequence":f"{ctx.get_current_key()}:{timestamp}","expired_at_ms":timestamp})

    def close(self):
        """Retain the journal on disk after the operator closes."""
        if self.ledger is not None:
            self.ledger.close()


class WindowAggregate(AggregateFunction):
    """Accumulate bounded-size count/sum/max/availability state for each machine/window."""

    def create_accumulator(self):
        """Start an empty incremental accumulator."""
        return 0, 0.0, 0.0, 0

    def add(self, value, accumulator):
        """Accumulate one accepted reading and its simulated publication time."""
        row = json.loads(value)
        event = row["event"]
        return (accumulator[0] + 1, accumulator[1] + event["temperature_c"],
                max(accumulator[2], event["vibration_rms_mm_s"]), max(accumulator[3], millis(row["published_ts"])))

    def get_result(self, accumulator):
        """Return count and sufficient statistics without retaining the full window."""
        return accumulator

    def merge(self, first, second):
        """Combine partial aggregates without averaging averages."""
        return first[0] + second[0], first[1] + second[1], max(first[2], second[2]), max(first[3], second[3])


class WindowResult(ProcessWindowFunction):
    """Attach half-open window bounds and availability to the incremental result."""

    def process(self, key, context, elements):
        """Report final aggregates; their event timestamp is not a promise of availability at that time."""
        count, total, vibration, available = next(iter(elements))
        yield json.dumps({"kind": "window", "machine_id": key,
                          "window_start": utc_millis(context.window().start),
                          "event_timestamp": utc_millis(context.window().end),
                          "created": datetime.now(timezone.utc).isoformat(),
                          "max_input_published_ts": utc_millis(available), "closing_watermark_ms": context.current_watermark(),
                          "reading_count": count, "temperature_sum": total,
                          "temperature_mean": total / count, "vibration_max": vibration})


class PersistResult(MapFunction):
    """Write keyed absolute outputs to a local sink; checkpoint transactions are outside this milestone."""

    def __init__(self, path: str, progress_path: str):
        """Retain only a serializable path in the submitted operator."""
        self.path = path
        self.progress_path = progress_path

    def open(self, runtime_context):
        """Open a single-writer SQLite connection on the Linux Docker volume."""
        self.store = StreamStore(Path(self.path))

    def map(self, value):
        """Commit each output, then expose progress independently of the database snapshot."""
        row = json.loads(value)
        self.store.write(row)
        if row["kind"] == "progress":
            target = Path(self.progress_path).with_name(f"{Path(self.progress_path).stem}-progress-{row['marker']}.json")
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(row))
            temporary.replace(target)
        if row["kind"] == "expired":
            key = sha256(row["event_id"].encode()).hexdigest()[:12]
            target = Path(self.progress_path).with_name(f"{Path(self.progress_path).stem}-expired-{key}.json")
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(row))
            temporary.replace(target)
        return ""

    def close(self):
        """Flush writes on a normally completed bounded job."""
        self.store.close()


def main() -> None:
    """Execute one finite Kafka workload in streaming mode with explicit runtime configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--records", required=True, type=int)
    parser.add_argument("--database", required=True)
    parser.add_argument("--runtime-report", required=True)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--dedup-retention-ms",type=int,default=7*86400*1000)
    parser.add_argument("--checkpoint-interval-ms",type=int,default=5000)
    args = parser.parse_args()
    config = Configuration()
    for key, value in {"rest.port": "8081", "rest.bind-address": "0.0.0.0", "local.start-webserver": "true",
                       "taskmanager.memory.process.size": "2048m", "parallelism.default": "1",
                       "state.backend.type": "rocksdb", "state.backend.rocksdb.memory.fixed-per-slot": "128m",
                       "execution.checkpointing.storage": "filesystem",
                       "execution.checkpointing.dir": (Path(args.database).resolve().parent / (Path(args.database).stem+'-checkpoints')).as_uri(),
                       "python.executable": "python3", "python.client.executable": "python3",
                       "python.fn-execution.bundle.size": "100", "python.fn-execution.bundle.time": "100"}.items():
        config.set_string(key, value)
    env = StreamExecutionEnvironment.get_execution_environment(config)
    env.set_parallelism(1)
    env.set_restart_strategy(RestartStrategies.no_restart())
    env.get_config().set_auto_watermark_interval(100)
    env.enable_checkpointing(args.checkpoint_interval_ms)
    source = (KafkaSource.builder().set_bootstrap_servers("kafka:9092").set_topics(args.topic)
              .set_group_id(f"iot-{args.topic}").set_starting_offsets(KafkaOffsetsInitializer.earliest())
              .set_bounded(KafkaOffsetsInitializer.offsets({KafkaTopicPartition(args.topic, 0): args.records}))
              .set_value_only_deserializer(SimpleStringSchema()).build())
    delay = 0 if args.baseline else 15
    strategy = WatermarkStrategy.for_bounded_out_of_orderness(Duration.of_minutes(delay)).with_timestamp_assigner(EventTimestamp())
    readings = (env.from_source(source, WatermarkStrategy.no_watermarks(), "Kafka telemetry source")
                .assign_timestamps_and_watermarks(strategy)).key_by(
        lambda value: json.loads(value)["event"]["event_id"] if json.loads(value)["kind"] == "telemetry" else f"clock-{json.loads(value)['marker']}")
    export_path=Path(args.database).resolve()
    ledger_path=export_path.with_name(export_path.stem+'-dedup.sqlite')
    local_ledger=volume_store_path(ledger_path)
    local_result=volume_store_path(export_path)
    dedup = readings.process(Deduplicate(not args.baseline,args.dedup_retention_ms,str(local_ledger)), output_type=Types.STRING()).name("Immutable event dedup and watermark audit")
    windows = (dedup.key_by(lambda value: json.loads(value)["event"]["machine_id"])
               .window(TumblingEventTimeWindows.of(Time.minutes(10))).side_output_late_data(LATE)
               .aggregate(WindowAggregate(), WindowResult(),
                          accumulator_type=Types.TUPLE([Types.LONG(), Types.DOUBLE(), Types.DOUBLE(), Types.LONG()]),
                          output_type=Types.STRING()).name("10 minute event time aggregate"))
    late = windows.get_side_output(LATE).map(lambda value: json.dumps({**json.loads(value), "kind": "late"}), output_type=Types.STRING())
    output = windows.union(dedup.get_side_output(AUDIT), late)
    output.map(PersistResult(str(local_result),str(export_path)), output_type=Types.STRING()).name("Idempotent local result sink").filter(lambda _: False).print()
    started = time.perf_counter()
    result = env.execute(f"iot-{'baseline' if args.baseline else 'corrected'}-{args.topic}")
    export_sqlite(local_result,export_path)
    if not args.baseline:
        export_sqlite(local_ledger,ledger_path)
    report = {"passed": True, "job_id": str(result.get_job_id()), "flink": "1.20.3", "python": platform.python_version(),
              "job_runtime_ms": result.get_net_runtime(), "wall_seconds": time.perf_counter() - started,
              "records": args.records, "watermark_delay_minutes": delay, "dedup_retention_processing_ms": args.dedup_retention_ms,
              "retention_implementation":"processing-time expiry plus state-clearing timer, no native StateTtlConfig",
              "replay_protection_after_expiry":"persistent local event_id ledger, retained for the scenario lifetime",
              "sqlite_commit_policy":"each write, WAL synchronous=NORMAL on Linux volume; explicit post-job snapshots",
              "result_snapshot_available_utc":datetime.now(timezone.utc).isoformat(),
              "checkpoint_interval_ms":args.checkpoint_interval_ms,
              "state_backend": "EmbeddedRocksDBStateBackend",
              "scope": "bounded offsets, streaming execution, one Kafka partition and local Flink parallelism=1; no crash recovery/exactly-once claim"}
    Path(args.runtime_report).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
