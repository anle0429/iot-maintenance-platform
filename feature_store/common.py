"""Configure a bounded Feast SDK process and serialize small verifiable evidence."""

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path


def digest(path: Path) -> str:
    """Hash an artifact incrementally so source size does not determine SDK memory use."""
    value = sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def save(path: Path, value: dict):
    """Keep evidence UTF8/LF and disallow NaN masquerading as a valid measurement."""
    def encode(item):
        """Represent audit/simulated timestamps explicitly without changing their clocks."""
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError(type(item).__name__)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(value, default=encode, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    pending.replace(path)


def create_store(project: str, directory: Path, key_ttl_seconds: int = 3600):
    """Use the native DuckDB/Redis providers with one bounded DuckDB connection."""
    import ibis
    from feast import FeatureStore
    from feast.repo_config import RepoConfig
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / 'duckdb-temp'
    temp.mkdir(exist_ok=True)
    backend = ibis.duckdb.connect()
    backend.raw_sql("SET TimeZone='UTC'")
    backend.raw_sql("SET threads=1")
    backend.raw_sql("SET memory_limit='3GB'")
    backend.raw_sql("SET temp_directory='" + str(temp).replace("'", "''") + "'")
    ibis.set_backend(backend)
    config = RepoConfig(project=project, provider='local', registry=str(directory / 'registry.db'),
                        offline_store={'type': 'duckdb'},
                        online_store={'type': 'redis', 'connection_string': os.environ['IOT_FEAST_REDIS'],
                                      'key_ttl_seconds': key_ttl_seconds})
    return FeatureStore(config=config), backend


def utc_now() -> str:
    """Return wall-clock audit time, separate from the source's simulated event clock."""
    return datetime.now(timezone.utc).isoformat()


def utc_microseconds(frame, columns):
    """Normalize Spark-compatible UTC precision and reject any silent submicrosecond loss."""
    import pandas as pd
    result = frame.copy()
    for column in columns:
        before = pd.to_datetime(result[column], utc=True)
        after = before.dt.as_unit('us')
        if not (before.isna() | (before == after)).all():
            raise ValueError('Submicrosecond timestamps cannot be represented losslessly')
        result[column] = after
    return result
