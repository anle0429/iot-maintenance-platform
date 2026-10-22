"""Verify independent time/availability oracles and source preparation on small known data."""

import csv
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from generator.config import Config, parse_ts
from generator.simulation import generate
from generator.storage import write_dataset
from ml.evaluation import choose_threshold, classification_metrics
from pipelines.source_loader import machine_change_rows, prepare_import, validate_run_id
from pipelines.temporal import available_window, label_snapshot, temporal_split


class TemporalContractTests(unittest.TestCase):
    """Specify label boundaries and information availability without importing Spark."""

    def setUp(self) -> None:
        """Use a UTC snapshot with an observed future longer than one hour."""
        self.at = parse_ts("2026-10-01T01:00:00Z")
        self.end = self.at + timedelta(hours=3)

    def test_failure_at_one_hour_is_positive(self) -> None:
        """The upper horizon endpoint is included."""
        failures = [{"machine_id": "P", "failure_start_ts": self.at + timedelta(hours=1)}]
        self.assertEqual(label_snapshot("P", self.at, "operating", failures, [], self.end), (1, "eligible"))

    def test_failure_after_horizon_is_negative(self) -> None:
        """One second beyond the horizon does not label the current snapshot positive."""
        failures = [{"machine_id": "P", "failure_start_ts": self.at + timedelta(seconds=3601)}]
        self.assertEqual(label_snapshot("P", self.at, "operating", failures, [], self.end), (0, "eligible"))

    def test_already_failed_snapshot_is_ineligible(self) -> None:
        """A failure at t is current downtime, not a future positive training example."""
        failure = [{"machine_id": "P", "failure_start_ts": self.at}]
        self.assertEqual(label_snapshot("P", self.at, "failed", failure, [], self.end), (None, "stopped"))

    def test_other_machine_failure_does_not_change_label(self) -> None:
        """Labels are per machine rather than shared across the plant."""
        failure = [{"machine_id": "OTHER", "failure_start_ts": self.at + timedelta(minutes=30)}]
        self.assertEqual(label_snapshot("P", self.at, "operating", failure, [], self.end), (0, "eligible"))

    def test_observation_end_is_exclusive(self) -> None:
        """Never turn an unobserved horizon tail into a negative label."""
        for end in (self.at + timedelta(minutes=59), self.at + timedelta(hours=1)):
            with self.subTest(end=end):
                self.assertEqual(label_snapshot("P", self.at, "operating", [], [], end), (None, "censored"))

    def test_maintenance_intervention_censors_even_a_positive(self) -> None:
        """The conservative intervention policy removes a horizon with maintenance."""
        failure = [{"machine_id": "P", "failure_start_ts": self.at + timedelta(minutes=30)}]
        maintenance = [{"machine_id": "P", "start_ts": self.at + timedelta(hours=1)}]
        self.assertEqual(label_snapshot("P", self.at, "operating", failure, maintenance, self.end), (None, "maintenance"))

    def test_reading_requires_event_time_and_arrival_time(self) -> None:
        """Late/future readings and the exclusive old boundary cannot enter the feature."""
        rows = [{"id": "old", "event_ts": self.at - timedelta(minutes=10), "received_ts": self.at - timedelta(minutes=10)},
                {"id": "known", "event_ts": self.at - timedelta(minutes=9), "received_ts": self.at},
                {"id": "late", "event_ts": self.at - timedelta(minutes=1), "received_ts": self.at + timedelta(seconds=1)},
                {"id": "future", "event_ts": self.at + timedelta(seconds=1), "received_ts": self.at + timedelta(seconds=1)}]
        self.assertEqual([row["id"] for row in available_window(rows, self.at)], ["known"])

    def test_label_availability_purges_split_boundaries(self) -> None:
        """A label available exactly at the next partition is excluded from the previous one."""
        train_end, val_end = self.at + timedelta(hours=2), self.at + timedelta(hours=3)
        self.assertEqual(temporal_split(self.at, train_end - timedelta(seconds=1), train_end, val_end), "train")
        self.assertIsNone(temporal_split(self.at, train_end, train_end, val_end))
        self.assertEqual(temporal_split(train_end, val_end - timedelta(seconds=1), train_end, val_end), "validation")
        self.assertIsNone(temporal_split(train_end, val_end, train_end, val_end))
        self.assertEqual(temporal_split(val_end, val_end + timedelta(hours=1), train_end, val_end), "test")


class SourcePreparationTests(unittest.TestCase):
    """Check source imports retain offline duplicates, exclude replay copies and preserve originals."""

    def setUp(self) -> None:
        """Use sixty readings and no metadata intervention by default."""
        self.config = Config(machine_count=1, duration_hours=1)
        self.machine = {"machine_id": "P-001", "factory_id": "F-01", "valid_from_ts": "2026-10-01T00:00:00Z"}
        self.change = {"machine_id": "P-001", "factory_id": "F-04", "effective_ts": "2026-10-01T00:30:00Z", "recorded_ts": "2026-10-01T00:35:00Z"}

    def test_import_counts_have_no_stream_copy(self) -> None:
        """CSV rows equal original offline rows plus an explicit source metadata change."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, imports = root / "source", root / "imports"
            write_dataset(generate(self.config), self.config, source)
            before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
            report = prepare_import(source, "fixture", [self.change], imports)
            with (imports / "fixture.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(report["expected_records"], 63)
            self.assertEqual(len(rows), 63)
            self.assertFalse(any(row[1].startswith("stream/") for row in rows))
            telemetry = [json.loads(row[3]) for row in rows if row[1].startswith("telemetry/")]
            self.assertEqual(len(telemetry), 61)
            self.assertEqual(len({row["event_id"] for row in telemetry}), 60)
            for path, content in before.items():
                self.assertEqual((source / path).read_bytes(), content)

    def test_metadata_effect_and_availability_are_distinct(self) -> None:
        """A snapshot can become effective before its department update is recorded."""
        row = machine_change_rows([self.machine], [self.change], self.config)[0]
        self.assertEqual(row["factory_id"], "F-04")
        self.assertEqual(row["valid_from_ts"], self.change["effective_ts"])
        self.assertEqual(row["recorded_ts"], self.change["recorded_ts"])
        self.assertEqual(self.machine["factory_id"], "F-01")

    def test_invalid_changes_are_rejected(self) -> None:
        """Unknown machines, out-of-range effects, early recording and duplicate changes fail."""
        cases = [[{**self.change, "machine_id": "UNKNOWN"}],
                 [{**self.change, "effective_ts": "2026-10-01T01:00:00Z"}],
                 [{**self.change, "recorded_ts": "2026-10-01T00:29:00Z"}], [self.change, self.change]]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                machine_change_rows([self.machine], changes, self.config)

    def test_run_id_cannot_escape_path_or_sql(self) -> None:
        """Dataset names cannot add SQL statements or traverse filesystem directories."""
        for value in ("../outside", "x';DROP TABLE source_records;--", "", "x" * 65):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_run_id(value)


class EvaluationTests(unittest.TestCase):
    """Verify confusion counts and validation threshold selection on hand-computed scores."""

    def test_known_confusion_matrix(self) -> None:
        """Two correct and two incorrect decisions give precision/recall/F1 of one half."""
        metrics = classification_metrics([1, 0, 1, 0], [0.9, 0.8, 0.2, 0.1], 0.5)
        self.assertEqual([metrics[key] for key in ("tp", "fp", "fn", "tn")], [1, 1, 1, 1])
        self.assertEqual([metrics[key] for key in ("precision", "recall", "f1")], [0.5, 0.5, 0.5])

    def test_exact_threshold_and_no_alarms(self) -> None:
        """A score at the threshold triggers an alert; an empty alert set has zero precision."""
        self.assertEqual(classification_metrics([1], [0.5], 0.5)["tp"], 1)
        self.assertEqual(classification_metrics([1, 0], [0.1, 0.1], 0.5)["precision"], 0)

    def test_threshold_is_chosen_from_validation_f1(self) -> None:
        """The known validation fixture has best F1 at threshold 0.2."""
        selected = choose_threshold([1, 0, 1, 0], [0.9, 0.8, 0.2, 0.1])
        self.assertEqual(selected["threshold"], 0.2)
        self.assertAlmostEqual(selected["f1"], 0.8)

    def test_invalid_probability_inputs_fail(self) -> None:
        """Do not silently evaluate missing labels or nonfinite/out-of-range probabilities."""
        for labels, scores, threshold in (([], [], 0.5), ([1], [], 0.5), ([2], [0.5], 0.5),
                                          ([1], [float("nan")], 0.5), ([1], [1.1], 0.5), ([1], [0.5], -0.1)):
            with self.subTest(labels=labels, scores=scores), self.assertRaises(ValueError):
                classification_metrics(labels, scores, threshold)


if __name__ == "__main__":
    unittest.main()
