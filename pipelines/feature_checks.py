"""Verify bounded temporal joins against independent Python answers and the range baseline."""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import platform
from statistics import fmean, pstdev
import time

from pyspark.sql import SparkSession

from pipelines import bounded_features, features
from pipelines.temporal import label_snapshot


def _fixture():
    """Include microsecond endpoints, midnight, pre-epoch, late arrivals and empty windows."""
    origin = datetime(2026, 10, 2, tzinfo=timezone.utc)
    before_epoch = datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
    points = [origin - timedelta(microseconds=1), origin, origin + timedelta(minutes=10),
              origin + timedelta(hours=1), origin + timedelta(hours=24)]
    snapshots = [("P", at, "operating") for at in points]
    snapshots += [("Q", before_epoch, "operating"), ("empty", origin, "operating"),
                  ("stopped", origin, "failed"), ("tail", origin + timedelta(days=2), "operating")]
    readings, failures, maintenance = [], [], []
    for index, (machine, at, _) in enumerate(snapshots):
        if machine == "empty":
            continue
        for offset_us in (-600000001, -600000000, -599999999, -1000000, 0, 1, 600000000):
            event = at + timedelta(microseconds=offset_us)
            for delay in (0, 1, 601):
                value = float(index * 10 + len(readings) % 9)
                readings.append({"event_id": f"r{len(readings)}", "machine_id": machine,
                    "event_ts": event, "received_ts": event + timedelta(seconds=delay),
                    "temperature_c": value, "vibration_rms_mm_s": value + 1,
                    "current_a": value + 2, "rpm": value + 3})
        for offset_us in (-86400000001, -86400000000, -86399999999, -1, 0, 1, 3600000000, 3600000001):
            event = at + timedelta(microseconds=offset_us)
            for delay in (0, 30, 86401):
                failures.append({"failure_id": f"f{len(failures)}", "machine_id": machine,
                    "failure_start_ts": event, "recorded_ts": event + timedelta(seconds=delay)})
    maintenance += [{"maintenance_id": f"m{i}", "machine_id": "P",
                     "start_ts": origin + timedelta(microseconds=delta)}
                    for i, delta in enumerate((0, 1, 3600000000, 3600000001))]
    machines = [{"machine_id": machine, "installed_at": (before_epoch if machine == "Q" else origin) - timedelta(days=365),
                 "valid_from_ts": (before_epoch if machine == "Q" else origin) - timedelta(days=1)}
                for machine in sorted({s[0] for s in snapshots})]
    return snapshots, readings, machines, failures, maintenance, origin + timedelta(days=2)


def _naive(value):
    """Compare Spark's timezone-free collected UTC timestamps with the aware Python oracle."""
    return value.replace(tzinfo=None) if isinstance(value, datetime) else value


def _equal(actual, expected):
    """Allow aggregate floating-point roundoff, but require exact keys, counts, timestamps and nulls."""
    if isinstance(expected, float):
        return isinstance(actual, (float, int)) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10)
    return _naive(actual) == _naive(expected)


def check(spark) -> dict:
    """Materialize both strategies on tiny independently specified temporal edge cases."""
    snapshots, readings, machines, failures, maintenance, observation_end = _fixture()
    snapshot_frame = spark.createDataFrame(snapshots, "machine_id string, event_timestamp timestamp, operating_status string")
    telemetry = spark.createDataFrame(readings, "event_id string, machine_id string, event_ts timestamp, received_ts timestamp, temperature_c double, vibration_rms_mm_s double, current_a double, rpm double")
    machine_frame = spark.createDataFrame(machines, "machine_id string, installed_at timestamp, valid_from_ts timestamp")
    failure_frame = spark.createDataFrame(failures, "failure_id string, machine_id string, failure_start_ts timestamp, recorded_ts timestamp")
    maintenance_frame = spark.createDataFrame(maintenance, "maintenance_id string, machine_id string, start_ts timestamp")
    expected_features, expected_labels = {}, {}
    for machine, at, status in snapshots:
        key = (machine, _naive(at))
        window = [r for r in readings if r["machine_id"] == machine
                  and at - timedelta(minutes=10) < r["event_ts"] <= at and r["received_ts"] <= at]
        initial = next(m for m in machines if m["machine_id"] == machine)
        expected_features[key] = {"temperature_mean_10m": fmean(r["temperature_c"] for r in window) if window else None,
            "vibration_max_10m": max((r["vibration_rms_mm_s"] for r in window), default=None),
            "current_mean_10m": fmean(r["current_a"] for r in window) if window else None,
            "current_std_10m": pstdev(r["current_a"] for r in window) if window else 0.0,
            "rpm_mean_10m": fmean(r["rpm"] for r in window) if window else None,
            "reading_count_10m": len(window),
            "machine_age_days": (math.floor(at.timestamp()) - math.floor(initial["installed_at"].timestamp())) / 86400,
            "prior_failures_24h": sum(f["machine_id"] == machine and at - timedelta(days=1) < f["failure_start_ts"] <= at
                                     and f["recorded_ts"] <= at for f in failures),
            "max_received_ts": max((r["received_ts"] for r in window), default=None),
            "max_feature_event_ts": max((r["event_ts"] for r in window), default=None)}
        label, label_status = label_snapshot(machine, at, status, failures, maintenance, observation_end)
        end = at + timedelta(hours=1)
        expected_labels[key] = {"failure_in_next_1h": label, "label_status": label_status,
            "next_failure_ts": min((f["failure_start_ts"] for f in failures if f["machine_id"] == machine
                                    and at < f["failure_start_ts"] <= end), default=None),
            "interventions": sum(m["machine_id"] == machine and at < m["start_ts"] <= end for m in maintenance),
            "label_end_ts": end, "label_available_ts": end + timedelta(seconds=30)}
    checks, plans = {}, {}
    for name, module in (("range", features), ("bucketed", bounded_features)):
        computed = module.compute_features(snapshot_frame.select("machine_id", "event_timestamp"), telemetry, machine_frame, failure_frame)
        labels = module.label_snapshots(snapshot_frame, failure_frame, maintenance_frame, observation_end.isoformat())
        for kind, frame, expected in (("features", computed, expected_features), ("labels", labels, expected_labels)):
            rows = frame.collect()
            checks[f"{name}_{kind}_grain"] = len(rows) == len(expected) and {(r.machine_id, r.event_timestamp) for r in rows} == set(expected)
            mismatches = [(r.machine_id, str(r.event_timestamp), col, str(r[col]), str(value))
                          for r in rows for col, value in expected[(r.machine_id, r.event_timestamp)].items() if not _equal(r[col], value)]
            if mismatches:
                raise AssertionError(f"{name}/{kind} differs from independent answers: {mismatches[:8]}")
            checks[f"{name}_{kind}_oracle"] = True
            plans[f"{name}_{kind}"] = frame._jdf.queryExecution().executedPlan().toString()
    assert all(checks.values()), checks
    return {"passed": True, "checks": checks, "snapshot_rows": len(snapshots),
            "telemetry_rows": len(readings), "failure_rows": len(failures), "maintenance_rows": len(maintenance),
            "plans": plans, "scope": "Tiny temporal correctness fixture; no production switch, performance, medium-scale feature or ML quality claim."}


def main():
    """Write immutable reports and physical plans only after a successful real Spark check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Choose a new fixture output directory")
    args.output.mkdir(parents=True)
    started = time.monotonic()
    spark = SparkSession.builder.appName("iot-feature-bucket-checks").getOrCreate()
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    try:
        report = check(spark)
        report.update({"spark": spark.version, "python": platform.python_version(),
            "application_id": spark.sparkContext.applicationId, "elapsed_seconds": time.monotonic() - started,
            "checked_utc": datetime.now(timezone.utc).isoformat(),
            "code_sha256": {f"pipelines/{name}.py": sha256(Path(__file__).with_name(name + ".py").read_bytes()).hexdigest()
                            for name in ("features", "bounded_features", "feature_checks", "temporal")}})
        for name, plan in report.pop("plans").items():
            (args.output / (name + "-plan.txt")).write_text(plan, encoding="utf-8")
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k != "code_sha256"}, indent=2), flush=True)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
