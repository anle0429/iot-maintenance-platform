"""Test data invariants with small known workloads, corruption and boundary cases."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from generator.config import Config, iso_ts, parse_ts
from generator.quality import inspect_source, summarize
from generator.simulation import generate
from generator.storage import write_dataset


class GeneratorTests(unittest.TestCase):
    """Exercise behavior visible to future data consumers rather than implementation details."""

    def setUp(self) -> None:
        """Use 60 known machine-minute observations with exact anomaly counts."""
        self.config = Config(machine_count=1, duration_hours=1, late_rate=0.1,
                             offline_duplicate_rate=0.1, stream_duplicate_rate=0.05)

    def test_known_counts_and_cardinality(self) -> None:
        """The 60-row fixture must contain six offline extras and three stream retries."""
        report = summarize(generate(self.config), self.config)
        self.assertTrue(report["passed"], report["checks"])
        self.assertEqual(report["unique_telemetry_rows"], 60)
        self.assertEqual(report["offline_rows"], 66)
        self.assertEqual(report["stream_deliveries"], 63)
        self.assertEqual(report["late_unique_events"], 6)
        self.assertAlmostEqual(report["offline_duplicate_fraction_of_stored_rows"], 6 / 66)
        self.assertEqual(report["cardinality_on_offline_rows"]["event_id"]["unique"], 60)

    def test_seed_produces_identical_content(self) -> None:
        """Identical configuration produces identical in-memory source tables."""
        self.assertEqual(generate(self.config), generate(self.config))

    def test_different_seed_changes_measurements(self) -> None:
        """A different seed changes telemetry, not merely the run metadata."""
        left = generate(self.config).telemetry[0]
        right = generate(replace(self.config, seed=43)).telemetry[0]
        self.assertNotEqual(left["temperature_c"], right["temperature_c"])

    def test_transport_settings_do_not_change_physical_history(self) -> None:
        """Network anomalies cannot change source failures or measured sensor values."""
        config = replace(self.config, duration_hours=3, wear_per_hour=2)
        left = generate(config)
        right = generate(replace(config, late_rate=1, offline_duplicate_rate=1, stream_duplicate_rate=1))
        self.assertEqual(left.failures, right.failures)
        measurements = lambda d: {(r["event_id"], r["temperature_c"], r["rpm"]) for r in d.telemetry}
        self.assertEqual(measurements(left), measurements(right))

    def test_anomaly_rate_boundaries(self) -> None:
        """Zero and full rates preserve keys while selecting exact expected counts."""
        for rate in (0.0, 1.0):
            with self.subTest(rate=rate):
                config = replace(self.config, late_rate=rate, offline_duplicate_rate=rate, stream_duplicate_rate=rate)
                report = summarize(generate(config), config)
                self.assertTrue(report["passed"], report["checks"])
                self.assertEqual(report["late_unique_events"], 60 * rate)
                self.assertEqual(report["offline_duplicate_extras"], 60 * rate)
                self.assertEqual(report["stream_duplicate_extras"], 60 * rate)

    def test_fractional_anomaly_counts_round_down(self) -> None:
        """The configured denominator is unique events and counts use floor, not probability."""
        config = replace(self.config, offline_duplicate_rate=0.019)
        self.assertEqual(summarize(generate(config), config)["offline_duplicate_extras"], 1)

    def test_schema_transition_boundaries(self) -> None:
        """The added pressure column is physically absent in v1, present in v2."""
        for fraction, expected_v2 in ((0, 60), (0.5, 30), (1, 0)):
            with self.subTest(fraction=fraction):
                config = replace(self.config, schema_v2_fraction=fraction, offline_duplicate_rate=0)
                rows = generate(config).telemetry
                self.assertEqual(sum("pressure_bar" in row for row in rows), expected_v2)
                self.assertTrue(summarize(generate(config), config)["passed"])

    def test_hot_factory_rounding(self) -> None:
        """Seven of ten machines produce exactly 70% of unique readings at F-01."""
        config = replace(self.config, machine_count=10)
        self.assertEqual(summarize(generate(config), config)["factory_unique_counts"]["F-01"], 420)

    def test_wear_controls_failures(self) -> None:
        """High wear causes real stop events while negligible wear has no failures."""
        low = generate(replace(self.config, wear_per_hour=0.00001))
        high_config = replace(self.config, duration_hours=3, wear_per_hour=2)
        high = generate(high_config)
        self.assertEqual(len(low.failures), 0)
        self.assertGreater(len(high.failures), 0)
        self.assertTrue(summarize(high, high_config)["passed"])

    def test_preventive_maintenance_stops_and_restarts_motor(self) -> None:
        """Maintenance has a recorded interval and telemetry returns to operating afterward."""
        config = replace(self.config, duration_hours=3, maintenance_every_hours=1, wear_per_hour=0.00001)
        dataset = generate(config)
        self.assertEqual(len(dataset.maintenance), 2)
        self.assertTrue(summarize(dataset, config)["passed"])
        self.assertEqual(dataset.telemetry[-1]["operating_status"], "operating")

    def test_unobserved_recovery_is_null(self) -> None:
        """A stop beyond the observation boundary never exports a known future recovery."""
        config = replace(self.config, wear_per_hour=100, repair_minutes=120)
        dataset = generate(config)
        self.assertIsNone(dataset.failures[0]["recovered_ts"])
        self.assertTrue(summarize(dataset, config)["passed"])

    def test_stream_replay_dedup_oracle_is_idempotent(self) -> None:
        """Replaying all deliveries twice must still yield the same 60 event IDs."""
        deliveries = generate(self.config).stream
        first = {d["event"]["event_id"] for d in deliveries}
        second = {d["event"]["event_id"] for d in deliveries + deliveries}
        self.assertEqual(first, second)
        self.assertEqual(len(second), 60)

    def test_late_events_are_really_out_of_order(self) -> None:
        """Arrival ordering includes at least one event-time inversion on the known fixture."""
        self.assertGreater(summarize(generate(self.config), self.config)["stream_event_time_inversions"], 0)

    def test_validator_rejects_conflicting_duplicate(self) -> None:
        """A repeated event ID with a different sensor reading is a source conflict."""
        dataset = generate(self.config)
        row = deepcopy(dataset.telemetry[0])
        row["temperature_c"] += 5
        dataset.telemetry.append(row)
        self.assertFalse(summarize(dataset, self.config)["checks"]["offline_duplicates_keep_measurement"])

    def test_validator_rejects_missing_reading(self) -> None:
        """Removing all copies of one event violates the complete machine-time grid."""
        dataset = generate(self.config)
        event_id = dataset.telemetry[0]["event_id"]
        dataset.telemetry = [r for r in dataset.telemetry if r["event_id"] != event_id]
        self.assertFalse(summarize(dataset, self.config)["checks"]["complete_time_grid"])

    def test_validator_rejects_arrival_before_measurement(self) -> None:
        """Availability cannot precede the measurement timestamp."""
        dataset = generate(self.config)
        dataset.telemetry[-1]["received_ts"] = "2026-09-30T23:00:00Z"
        self.assertFalse(summarize(dataset, self.config)["checks"]["timestamp_order"])

    def test_validator_rejects_nonfinite_sensor(self) -> None:
        """A sensor NaN is invalid JSON/source data, never a successful quality check."""
        dataset = generate(self.config)
        dataset.telemetry[0]["rpm"] = float("nan")
        self.assertFalse(summarize(dataset, self.config)["checks"]["schema_keys_and_types"])

    def test_validator_rejects_label_leakage_column(self) -> None:
        """Latent truth and future labels cannot be exported inside telemetry."""
        dataset = generate(self.config)
        dataset.telemetry[0]["failure_in_next_1h"] = 1
        self.assertFalse(summarize(dataset, self.config)["checks"]["no_label_or_latent_wear_in_telemetry"])

    def test_validator_rejects_retry_before_arrival(self) -> None:
        """A stream envelope cannot be delivered before its first gateway receipt."""
        dataset = generate(self.config)
        dataset.stream[0]["published_ts"] = "2026-09-30T23:59:59Z"
        self.assertFalse(summarize(dataset, self.config)["checks"]["stream_retry_time"])

    def test_validator_rejects_missing_failure_truth(self) -> None:
        """A failed motor requires a corresponding physical failure interval."""
        config = replace(self.config, wear_per_hour=100)
        dataset = generate(config)
        dataset.failures.clear()
        self.assertFalse(summarize(dataset, config)["checks"]["operating_status_matches_events"])

    def test_invalid_config_partitions(self) -> None:
        """Reject negative rates, invalid types, grids, horizons and oversized workloads."""
        cases = [{"late_rate": -0.01}, {"late_rate": 1.01}, {"late_rate": float("nan")},
                 {"late_rate": True}, {"machine_count": 0}, {"duration_hours": 0}, {"seed": -1},
                 {"sample_seconds": 7}, {"sample_seconds": 120}, {"duration_hours": 1.5},
                 {"wear_per_hour": 0}, {"failure_threshold": float("inf")},
                 {"late_min_seconds": 601}, {"late_min_seconds": 3},
                 {"start_ts": "2026-10-01T00:00:00"}, {"machine_count": 10000}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.config, **changes)

    def test_timezone_normalization(self) -> None:
        """Equivalent offset timestamps normalize to exactly the same UTC instant."""
        self.assertEqual(iso_ts(parse_ts("2026-09-30T19:00:00-05:00")), "2026-10-01T00:00:00Z")

    def test_persisted_sources_are_reproducible_and_checked(self) -> None:
        """All bytes including manifests agree across fresh directories, then read-back passes."""
        with tempfile.TemporaryDirectory() as temp:
            left, right = Path(temp) / "left", Path(temp) / "right"
            write_dataset(generate(self.config), self.config, left)
            write_dataset(generate(self.config), self.config, right)
            for path in left.rglob("*"):
                if path.is_file():
                    self.assertEqual(path.read_bytes(), (right / path.relative_to(left)).read_bytes())
            self.assertTrue(inspect_source(left)["passed"])
            self.assertEqual(inspect_source(left)["source_total_bytes"], sum(p.stat().st_size for p in left.rglob("*") if p.is_file()))

    def test_source_overwrite_is_refused(self) -> None:
        """An existing run is preserved, even if it is an empty directory."""
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(FileExistsError):
            write_dataset(generate(self.config), self.config, Path(temp))

    def test_persisted_corruption_fails_manifest_validation(self) -> None:
        """A whitespace-only file mutation still invalidates immutable-source evidence."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            write_dataset(generate(self.config), self.config, root)
            path = root / "machines.jsonl"
            path.write_text(path.read_text() + " ", encoding="utf-8")
            # Use valid JSON whitespace on the same line, preserving parsability.
            path.write_text(path.read_text().replace("\n ", " \n"), encoding="utf-8")
            report = inspect_source(root)
            self.assertFalse(report["passed"])
            self.assertFalse(report["checks"]["file_integrity:machines.jsonl"])

    def test_unknown_configuration_option_is_rejected(self) -> None:
        """A misspelled option must fail rather than silently using its default."""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({"machine_cout": 1}), encoding="utf-8")
            with self.assertRaises(TypeError):
                Config.load(path)

    def test_cli_can_generate_inspect_compare_and_reject_overwrite(self) -> None:
        """A learner can run the public commands without installed packages or PYTHONPATH."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "config.json"
            config_path.write_text(json.dumps(self.config.to_dict()), encoding="utf-8")
            for name in ("left", "right"):
                result = subprocess.run([sys.executable, "-m", "generator", "generate", "--config", str(config_path),
                                         "--output", str(root / name)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            for arguments in (["inspect", "--source", str(root / "left"), "--report", str(root / "quality.json")],
                              ["compare", "--left", str(root / "left"), "--right", str(root / "right"), "--report", str(root / "compare.json")]):
                result = subprocess.run([sys.executable, "-m", "generator", *arguments], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([sys.executable, "-m", "generator", "generate", "--config", str(config_path),
                                     "--output", str(root / "left")], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_cli_reports_cannot_overwrite_source(self) -> None:
        """Inspect/compare must reject report paths inside either immutable source run."""
        with tempfile.TemporaryDirectory() as temp:
            left, right = Path(temp) / "left", Path(temp) / "right"
            write_dataset(generate(self.config), self.config, left)
            write_dataset(generate(self.config), self.config, right)
            before = (left / "manifest.json").read_bytes()
            cases = (["inspect", "--source", str(left), "--report", str(left / "manifest.json")],
                     ["compare", "--left", str(left), "--right", str(right), "--report", str(left / "manifest.json")],
                     ["compare", "--left", str(left), "--right", str(right), "--report", str(right / "manifest.json")])
            for arguments in cases:
                with self.subTest(command=arguments[0]):
                    result = subprocess.run([sys.executable, "-m", "generator", *arguments], capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
            self.assertEqual((left / "manifest.json").read_bytes(), before)
            self.assertEqual((right / "manifest.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
