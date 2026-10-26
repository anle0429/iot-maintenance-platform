"""Bind immutable DP artifacts to source bytes and reject unsafe/replayed partial writes."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re


STAGES = ('dp1', 'dp2', 'dp3')
TABLES = {
    'dp1': ('bronze/raw_telemetry', 'bronze/raw_machines', 'bronze/raw_failures', 'bronze/raw_maintenance'),
    'dp2': ('silver/stg_telemetry', 'silver/stg_failures', 'silver/stg_maintenance',
            'gold/dim_machine', 'gold/dim_factory', 'gold/fact_telemetry', 'gold/fact_failure', 'gold/fact_maintenance'),
    'dp3': ('features/feat_machine_10m', 'features/label_failure_1h'),
}


def utc_now() -> str:
    """Return an aware audit timestamp, independent of the simulated event time."""
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    """Hash large artifacts without loading a whole dataset into memory."""
    result = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def save(path: Path, value: dict) -> None:
    """Atomically replace a small report; Parquet directories themselves remain immutable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    pending.replace(path)


def stage_path(root: Path, batch: str, stage: str) -> Path:
    """Accept only named runs within the project, including when a parent is a symlink."""
    if not isinstance(batch, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', batch) or stage not in STAGES:
        raise ValueError('invalid batch/stage')
    base = (root / 'data/orchestrated').resolve()
    if not base.is_relative_to(root.resolve()):
        raise ValueError('artifact base escapes project')
    target = base / batch / stage
    if not target.resolve().is_relative_to(base):
        raise ValueError('artifact path escapes project')
    return target


def inventory(directory: Path) -> list[dict]:
    """Fingerprint every Parquet part; report files never contribute to their own hash."""
    return [{'path': path.relative_to(directory).as_posix(), 'bytes': path.stat().st_size, 'sha256': digest(path)}
            for path in sorted(directory.rglob('*.parquet'))]


def verified_manifest(directory: Path, require_validated: bool = True) -> dict:
    """Reject incomplete, corrupted or failed upstream materializations before consuming them."""
    manifest = json.loads((directory / '_INGESTED.json').read_text(encoding='utf-8'))
    if not manifest['files'] or manifest['files'] != inventory(directory):
        raise ValueError('materialized Parquet bytes changed or are incomplete')
    if require_validated:
        validation = json.loads((directory / 'validation.json').read_text(encoding='utf-8'))
        if validation.get('passed') is not True or validation['manifest_sha256'] != digest(directory / '_INGESTED.json'):
            raise ValueError('upstream validation missing, failed or belongs to different bytes')
    return manifest
