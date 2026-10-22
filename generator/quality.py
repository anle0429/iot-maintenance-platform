"""Measure persisted data and check source contracts, anomalies and physical event consistency."""

from collections import Counter, defaultdict
from datetime import timedelta
import json
import math
from pathlib import Path

from generator.config import Config, iso_ts, parse_ts
from generator.simulation import Dataset
from generator.storage import file_info, load_dataset


TELEMETRY_V1 = {"event_id", "machine_id", "event_ts", "received_ts", "created_ts", "schema_version",
                "temperature_c", "vibration_rms_mm_s", "current_a", "rpm", "operating_status"}
NUMERIC_FIELDS = ("temperature_c", "vibration_rms_mm_s", "current_a", "rpm")


def canonical_event(row: dict) -> str:
    """Compare immutable measurement payloads while allowing a newer offline export time."""
    return json.dumps({key: value for key, value in row.items() if key != "created_ts"}, sort_keys=True)


def summarize(dataset: Dataset, config: Config) -> dict:
    """Compute observed metrics and explicit pass/fail checks, with duplicate-aware denominators."""
    rows = dataset.telemetry
    by_id = defaultdict(list)
    for row in rows:
        by_id[row["event_id"]].append(row)
    # This local oracle models the proposed Silver rule; no Spark/Flink claim is made.
    unique = {event_id: max(group, key=lambda r: r["created_ts"]) for event_id, group in by_id.items()}
    values = list(unique.values())
    machines = {row["machine_id"]: row for row in dataset.machines}
    factory_counts = Counter(machines[row["machine_id"]]["factory_id"] for row in values if row["machine_id"] in machines)
    schemas = Counter(str(row["schema_version"]) for row in rows)
    delays = [(parse_ts(row["received_ts"]) - parse_ts(row["event_ts"])).total_seconds() for row in values]
    late_delays = [delay for delay in delays if delay >= config.late_min_seconds]
    deliveries = dataset.stream
    stream_counts = Counter(delivery["event"]["event_id"] for delivery in deliveries)
    unique_count = len(unique)
    offline_extras = len(rows) - unique_count
    stream_extras = len(deliveries) - len(stream_counts)
    checks = {}
    checks["unique_rows_match_config"] = unique_count == config.unique_rows
    checks["machine_primary_and_foreign_keys"] = len(machines) == len(dataset.machines) == config.machine_count and all(row["machine_id"] in machines for row in values)
    checks["one_reading_per_machine_time"] = len({(r["machine_id"], r["event_ts"]) for r in values}) == unique_count
    grid = {iso_ts(config.start + timedelta(seconds=i * config.sample_seconds)) for i in range(config.steps)}
    machine_times = defaultdict(set)
    for row in values:
        machine_times[row["machine_id"]].add(row["event_ts"])
    checks["complete_time_grid"] = set(machine_times) == set(machines) and all(times == grid for times in machine_times.values())
    checks["offline_duplicate_count"] = offline_extras == math.floor(config.unique_rows * config.offline_duplicate_rate)
    checks["offline_duplicates_keep_measurement"] = all(len({canonical_event(row) for row in group}) == 1 for group in by_id.values())
    checks["offline_duplicates_have_new_export_time"] = all(len({r["created_ts"] for r in group}) == len(group) for group in by_id.values())
    checks["schema_keys_and_types"] = all(
        set(row) == TELEMETRY_V1 | ({"pressure_bar"} if row["schema_version"] == 2 else set())
        and type(row["schema_version"]) is int and row["schema_version"] in (1, 2)
        and all(type(row[field]) in (int, float) and math.isfinite(row[field]) and row[field] >= 0
                for field in NUMERIC_FIELDS + (("pressure_bar",) if row["schema_version"] == 2 else ()))
        and row["operating_status"] in ("operating", "failed", "maintenance")
        for row in rows)
    transition = config.start + timedelta(seconds=math.floor(config.steps * config.schema_v2_fraction) * config.sample_seconds)
    checks["schema_transition_event_time"] = all(r["schema_version"] == (2 if parse_ts(r["event_ts"]) >= transition else 1) for r in values)
    checks["timestamp_order"] = all(parse_ts(r["created_ts"]) >= parse_ts(r["received_ts"]) >= parse_ts(r["event_ts"]) for r in rows)
    checks["late_count"] = len(late_delays) == math.floor(config.unique_rows * config.late_rate)
    checks["delay_ranges"] = all(0 <= delay <= 3 or config.late_min_seconds <= delay <= config.late_max_seconds for delay in delays)
    hot_count = math.floor(config.machine_count * config.hot_factory_share + 0.5)
    checks["factory_skew"] = factory_counts["F-01"] == hot_count * config.steps
    checks["stream_duplicate_count"] = stream_extras == math.floor(config.unique_rows * config.stream_duplicate_rate)
    checks["stream_unique_matches_offline"] = set(stream_counts) == set(unique)
    checks["stream_delivery_ids_unique"] = len({d["delivery_id"] for d in deliveries}) == len(deliveries)
    checks["stream_arrival_order"] = all(a["published_ts"] <= b["published_ts"] for a, b in zip(deliveries, deliveries[1:]))
    checks["stream_payload_matches_offline"] = all(d["event"]["event_id"] in unique and canonical_event(d["event"]) == canonical_event(unique[d["event"]["event_id"]])
                                                    and d["event"]["created_ts"] == d["event"]["received_ts"] for d in deliveries)
    checks["stream_retry_time"] = all(d["attempt"] in (1, 2) and parse_ts(d["published_ts"]) == parse_ts(d["event"]["received_ts"])
                                     + timedelta(seconds=(d["attempt"] - 1) * config.retry_delay_seconds) for d in deliveries)
    attempts = defaultdict(list)
    for delivery in deliveries:
        attempts[delivery["event"]["event_id"]].append(delivery["attempt"])
    checks["stream_retry_attempts"] = all(sorted(group) in ([1], [1, 2]) for group in attempts.values())
    checks["no_label_or_latent_wear_in_telemetry"] = all(not ({"wear", "failure_in_next_1h", "failure_start_ts", "recovered_ts"} & set(r)) for r in rows)
    intervals = defaultdict(list)
    event_keys = []
    for events, kind, start_key, end_key, id_key in (
        (dataset.failures, "failed", "failure_start_ts", "recovered_ts", "failure_id"),
        (dataset.maintenance, "maintenance", "start_ts", "end_ts", "maintenance_id")):
        for event in events:
            start = parse_ts(event[start_key])
            end = parse_ts(event[end_key]) if event[end_key] else config.end
            intervals[event["machine_id"]].append((start, end, kind))
            event_keys.append(event[id_key])
    checks["event_primary_and_foreign_keys"] = len(set(event_keys)) == len(event_keys) and set(intervals) <= set(machines)
    checks["event_intervals_valid"] = all(config.start <= start < end <= config.end for group in intervals.values() for start, end, _ in group)
    checks["stop_intervals_do_not_overlap"] = all(all(a[1] <= b[0] for a, b in zip(sorted(group), sorted(group)[1:])) for group in intervals.values())
    checks["operating_status_matches_events"] = all(
        r["operating_status"] == next((kind for start, end, kind in intervals[r["machine_id"]]
                                       if start <= parse_ts(r["event_ts"]) < end), "operating") for r in values)
    checks["stopped_motor_zero"] = all(r["rpm"] == r["current_a"] == 0 for r in values if r["operating_status"] != "operating")
    checks["machines_snapshot_scd2_bounds"] = all(r["valid_from_ts"] == iso_ts(config.start) and r["valid_to_ts"] is None
                                                and r["is_current"] is True for r in dataset.machines)
    return {"passed": all(checks.values()), "checks": checks,
            "unique_telemetry_rows": unique_count, "offline_rows": len(rows), "stream_deliveries": len(deliveries),
            "offline_duplicate_extras": offline_extras, "offline_extras_per_unique": offline_extras / unique_count if unique_count else None,
            "offline_duplicate_fraction_of_stored_rows": offline_extras / len(rows) if rows else None,
            "stream_duplicate_extras": stream_extras, "stream_extras_per_unique": stream_extras / unique_count if unique_count else None,
            "late_unique_events": len(late_delays), "late_fraction_of_unique": len(late_delays) / unique_count if unique_count else None,
            "late_delay_seconds_min": min(late_delays) if late_delays else None,
            "late_delay_seconds_max": max(late_delays) if late_delays else None,
            "factory_unique_counts": dict(sorted(factory_counts.items())), "schema_stored_counts": dict(sorted(schemas.items())),
            "schema_columns": {"v1": sorted(TELEMETRY_V1), "v2": sorted(TELEMETRY_V1 | {"pressure_bar"})},
            "cardinality_on_offline_rows": {field: {"unique": len({r[field] for r in rows}),
                                                    "unique_over_rows": len({r[field] for r in rows}) / len(rows) if rows else None}
                                             for field in ("event_id", "machine_id", "event_ts")},
            "operating_status_unique_counts": dict(Counter(r["operating_status"] for r in values)),
            "failure_events": len(dataset.failures), "maintenance_events": len(dataset.maintenance),
            "replay_dedup_unique_rows": len(stream_counts),
            "stream_event_time_inversions": sum(a["event"]["event_ts"] > b["event"]["event_ts"] for a, b in zip(deliveries, deliveries[1:]))}


def inspect_source(root: Path) -> dict:
    """Read source files again, validate checksums/row counts, and measure actual source bytes."""
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    config = Config(**manifest["config"])
    dataset = load_dataset(root)
    report = summarize(dataset, config)
    measured = []
    checks = report["checks"]
    for expected in manifest["files"]:
        path = root / expected["path"]
        actual = file_info(path, root)
        with path.open(encoding="utf-8") as source:
            actual["rows"] = sum(1 for _ in source)
        measured.append(actual)
        checks[f"file_integrity:{expected['path']}"] = actual == expected
    v1_rows = next(item["rows"] for item in measured if item["path"].startswith("telemetry/schema_v1/"))
    checks["schema_v1_file_partition"] = all(r["schema_version"] == 1 for r in dataset.telemetry[:v1_rows])
    checks["schema_v2_file_partition"] = all(r["schema_version"] == 2 for r in dataset.telemetry[v1_rows:])
    report.update({"passed": all(checks.values()), "files": measured, "format": manifest["format"],
                   "compression": manifest["compression"], "config": config.to_dict(),
                   "payload_bytes": sum(item["bytes"] for item in measured),
                   "manifest_bytes": (root / "manifest.json").stat().st_size})
    report["source_total_bytes"] = report["payload_bytes"] + report["manifest_bytes"]
    return report
