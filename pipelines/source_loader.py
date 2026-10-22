"""Prepare a validated department-source import and load it into the coursework PostgreSQL."""

import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

from generator.config import Config, iso_ts, parse_ts
from generator.quality import inspect_source
from generator.storage import read_jsonl, write_json


def validate_run_id(value: str) -> str:
    """Accept only simple dataset identifiers used in paths and SQL literals."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("run ID must contain 1..64 letters, digits, underscores or hyphens")
    return value


def machine_change_rows(machines: list[dict], changes: list[dict], config: Config) -> list[dict]:
    """Turn explicit source metadata changes into snapshots without rewriting day-1 telemetry."""
    current = {row["machine_id"]: row.copy() for row in machines}
    result = []
    seen = set()
    for change in sorted(changes, key=lambda row: row["effective_ts"]):
        machine_id = change["machine_id"]
        if machine_id not in current:
            raise ValueError(f"unknown changed machine: {machine_id}")
        if set(change) != {"machine_id", "effective_ts", "recorded_ts", "factory_id"}:
            raise ValueError("metadata changes require machine_id, effective_ts, recorded_ts, factory_id")
        effective, recorded = parse_ts(change["effective_ts"]), parse_ts(change["recorded_ts"])
        if not config.start < effective < config.end or recorded < effective or recorded >= config.end:
            raise ValueError("metadata change must be inside observation and recorded at/after effect")
        key = (machine_id, effective)
        if key in seen or not change["factory_id"]:
            raise ValueError("metadata change keys must be unique and factory_id nonempty")
        seen.add(key)
        row = {**current[machine_id], "factory_id": change["factory_id"],
               "valid_from_ts": iso_ts(effective), "recorded_ts": iso_ts(recorded),
               "valid_to_ts": None, "is_current": True}
        current[machine_id] = row
        result.append(row)
    return result


def prepare_import(source: Path, run_id: str, changes: list[dict], destination: Path) -> dict:
    """Validate original files and write a CSV import containing only offline source rows."""
    validate_run_id(run_id)
    quality = inspect_source(source)
    if not quality["passed"]:
        raise ValueError("source failed day-1 quality validation")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    config = Config(**manifest["config"])
    snapshots = machine_change_rows(read_jsonl(source / "machines.jsonl"), changes, config)
    destination.mkdir(parents=True, exist_ok=True)
    csv_path = destination / f"{run_id}.csv"
    counts = {}
    with csv_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        for item in manifest["files"]:
            if item["path"].startswith("stream/"):
                continue
            counts[item["path"]] = item["rows"]
            with (source / item["path"]).open(encoding="utf-8") as original:
                for number, line in enumerate(original, start=1):
                    writer.writerow((run_id, item["path"], number, line.strip()))
        counts["machine_changes.jsonl"] = len(snapshots)
        for number, row in enumerate(snapshots, start=1):
            writer.writerow((run_id, "machine_changes.jsonl", number, json.dumps(row, sort_keys=True)))
    source_manifest = {"original_manifest": manifest, "metadata_changes": changes,
                       "source_counts": counts, "expected_records": sum(counts.values()),
                       "import_sha256": sha256(csv_path.read_bytes()).hexdigest()}
    # The file name and run_id are validated; JSON is quoted as a SQL literal.
    manifest_literal = json.dumps(source_manifest, sort_keys=True).replace("'", "''")
    sql = ("\\set ON_ERROR_STOP on\nBEGIN;\n"
           f"DELETE FROM source_records WHERE source_run = '{run_id}';\n"
           f"DELETE FROM source_runs WHERE source_run = '{run_id}';\n"
           f"INSERT INTO source_runs VALUES ('{run_id}', '{manifest_literal}'::jsonb, now());\n"
           f"COPY source_records(source_run,source_path,source_line,payload) FROM '/imports/{run_id}.csv' WITH (FORMAT csv);\n"
           "COMMIT;\n")
    (destination / f"{run_id}.sql").write_text(sql, encoding="utf-8", newline="\n")
    write_json(destination / f"{run_id}-manifest.json", source_manifest)
    return source_manifest


def main() -> None:
    """Load a verified small dataset atomically into an isolated department-source database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--changes", type=Path, default=Path("generator/configs/machine_changes.json"))
    parser.add_argument("--docker", default="docker")
    args = parser.parse_args()
    destination = Path("outputs/source-load")
    manifest = prepare_import(args.source, args.run_id, json.loads(args.changes.read_text()), destination)
    command = [args.docker, "compose", "-f", "infra/compose.yaml", "exec", "-T", "source",
               "psql", "-U", "iot_ingest", "-d", "iot_source", "-v", "ON_ERROR_STOP=1"]
    subprocess.run(command + ["-f", f"/imports/{args.run_id}.sql"], check=True)
    result = subprocess.check_output(command + ["-At", "-c", f"SELECT count(*) FROM source_records WHERE source_run='{args.run_id}'"]).decode().strip()
    if int(result) != manifest["expected_records"]:
        raise ValueError("PostgreSQL record count differs from validated source")
    print(f"PASS: PostgreSQL source_run={args.run_id}, offline records={result}; replay uses transactional replace")


if __name__ == "__main__":
    main()
