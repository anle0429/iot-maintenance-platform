"""Protect predeclared split/selection rules and meaningful class/episode support gates."""

import json
from pathlib import Path
import tempfile
import unittest

from ml.protocol import class_support, load_protocol


PATH = Path(__file__).resolve().parents[1] / "ml/protocols/medium-60d-v1.json"


class ProtocolTests(unittest.TestCase):
    """Fail closed on edited holdout rules and insufficient independent events."""

    def setUp(self):
        """Load the immutable protocol for every independent boundary case."""
        self.protocol = load_protocol(PATH)

    def test_declared_protocol_loads(self):
        """The declared version remains usable without modifying its state after execution."""
        self.assertEqual(self.protocol["protocol_version"], "medium-60d-v1")
        self.assertFalse(self.protocol["threshold"]["test_reuse_for_selection"])

    def test_changed_cut_or_threshold_is_rejected(self):
        """An apparently valid JSON edit still invalidates the predeclared commitment."""
        for section, key, value in (("chronological_split", "train_end_exclusive", "2026-11-13T00:00:00Z"),
                                    ("threshold", "grid", [0.01, 0.99])):
            altered = json.loads(PATH.read_text())
            altered[section][key] = value
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "changed.json"
                path.write_text(json.dumps(altered))
                with self.assertRaisesRegex(ValueError, "frozen"):
                    load_protocol(path)

    def test_exact_minimum_is_supported(self):
        """All three independent lower bounds are inclusive."""
        counts = {"positive_rows": 20, "negative_rows": 20, "failure_episodes": 5}
        self.assertTrue(class_support({"validation": counts, "test": counts}, self.protocol)["passed"])

    def test_many_snapshots_do_not_replace_episodes(self):
        """Repeated snapshots before four failures are insufficient despite many positive rows."""
        sufficient = {"positive_rows": 1000, "negative_rows": 2000, "failure_episodes": 5}
        for key, value in (("positive_rows", 19), ("negative_rows", 19), ("failure_episodes", 4)):
            below = {**sufficient, key: value}
            result = class_support({"validation": sufficient, "test": below}, self.protocol)
            self.assertFalse(result["passed"])
            self.assertTrue(result["checks"]["validation"])

    def test_missing_partition_cannot_pass(self):
        """An absent holdout partition is not evidence of sufficient class support."""
        self.assertFalse(class_support({}, self.protocol)["passed"])

    def test_invalid_counts_are_rejected(self):
        """Do not accept Boolean, fractional, missing-sign or nonfinite measurements as counts."""
        for value in (-1, True, 20.0, float("nan"), "20"):
            with self.assertRaises(ValueError):
                class_support({"validation": {"positive_rows": value}}, self.protocol)


if __name__ == "__main__":
    unittest.main()
