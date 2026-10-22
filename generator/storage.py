"""Persist immutable JSONL source files, schemas and a deterministic checksum manifest."""

from hashlib import sha256
import json
from pathlib import Path

from generator import SIMULATION_VERSION
from generator.config import Config, iso_ts
from generator.simulation import Dataset


def write_json(path: Path, value: dict) -> None:
    """Write readable UTF-8 JSON with stable keys and LF newlines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")


def read_jsonl(path: Path) -> list[dict]:
    """Read a small JSONL source table without guessing dates or numeric types."""
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source]


def file_info(path: Path, root: Path) -> dict:
    """Measure file bytes and SHA-256 from its actual contents."""
    return {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
            "sha256": sha256(path.read_bytes()).hexdigest()}


def write_dataset(dataset: Dataset, config: Config, root: Path) -> dict:
    """Create a new source directory; refuse to overwrite any existing run."""
    root.mkdir(parents=True, exist_ok=False)
    tables = {"machines.jsonl": dataset.machines, "maintenance_events.jsonl": dataset.maintenance,
              "failure_events.jsonl": dataset.failures,
              "telemetry/schema_v1/part-00000.jsonl": [r for r in dataset.telemetry if r["schema_version"] == 1],
              "telemetry/schema_v2/part-00000.jsonl": [r for r in dataset.telemetry if r["schema_version"] == 2],
              "stream/telemetry_deliveries.jsonl": dataset.stream}
    files = []
    for name, rows in tables.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as output:
            for row in rows:
                output.write(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
        files.append({**file_info(path, root), "rows": len(rows)})
    manifest = {"simulation_version": SIMULATION_VERSION, "config": config.to_dict(),
                "observation_start_ts": iso_ts(config.start), "observation_end_exclusive_ts": iso_ts(config.end),
                "format": "JSONL", "compression": "none", "files": files}
    write_json(root / "manifest.json", manifest)
    return manifest


def load_dataset(root: Path) -> Dataset:
    """Load the persisted source files for independent post-generation validation."""
    telemetry = read_jsonl(root / "telemetry/schema_v1/part-00000.jsonl")
    telemetry += read_jsonl(root / "telemetry/schema_v2/part-00000.jsonl")
    return Dataset(read_jsonl(root / "machines.jsonl"), read_jsonl(root / "maintenance_events.jsonl"),
                   read_jsonl(root / "failure_events.jsonl"), telemetry,
                   read_jsonl(root / "stream/telemetry_deliveries.jsonl"))
