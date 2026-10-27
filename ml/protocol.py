"""Pin the predeclared evaluation protocol and assess class support before training."""

from hashlib import sha256
import json
from pathlib import Path


PROTOCOL_SHA256 = "e0bfcea57331b88bb6914f9342a9477eab6687f6a3929b8cb9834251fa9b8a96"


def load_protocol(path: Path) -> dict:
    """Refuse any byte change to the protocol committed before the new training run."""
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != PROTOCOL_SHA256:
        raise ValueError("Protocol differs from the frozen medium-60d-v1 declaration")
    return json.loads(raw)


def class_support(splits: dict, protocol: dict) -> dict:
    """Require both classes and independent failure episodes in validation and test."""
    gates = protocol["evaluation_gate"]
    checks = {}
    for split in ("validation", "test"):
        counts = splits.get(split, {})
        values = [counts.get(key, 0) for key in ("positive_rows", "negative_rows", "failure_episodes")]
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("Class support requires nonnegative integer counts")
        positive, negative, episodes = values
        checks[split] = (positive >= gates["validation_and_test_minimum_positive_rows"]
                         and negative >= gates["validation_and_test_minimum_negative_rows"]
                         and episodes >= gates["validation_and_test_minimum_distinct_failure_episodes"])
    return {"passed": all(checks.values()), "checks": checks,
            "insufficient_data_action": gates["insufficient_data_action"]}
