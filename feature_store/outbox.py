"""Persist finalized window intents so retries cannot blindly append duplicate offline features."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3


FIELDS = ['reading_count', 'temperature_sum', 'temperature_mean', 'vibration_max']


def window_payload(row: dict) -> tuple[str, str, dict]:
    """Validate a finalized half-open10-minute window; its transport creation time is not its identity."""
    if row.get('kind') != 'window' or not isinstance(row.get('machine_id'), str) or not row['machine_id']:
        raise ValueError('A finalized machine window is required')
    start = datetime.fromisoformat(row['window_start'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(row['event_timestamp'].replace('Z', '+00:00'))
    created = datetime.fromisoformat(row['created'].replace('Z', '+00:00'))
    if (any(t.tzinfo is None or t.utcoffset() != timedelta(0) for t in (start,end,created))
            or end-start != timedelta(minutes=10) or int(start.timestamp()) % 600 or start.microsecond):
        raise ValueError('Expected UTC aligned half-open10-minute window bounds')
    if type(row.get('reading_count')) is not int or row['reading_count'] < 1:
        raise ValueError('Reading count must be a positive integer')
    numbers = {n: float(row[n]) for n in FIELDS if n != 'reading_count'}
    if not all(math.isfinite(v) for v in numbers.values()) or numbers['vibration_max'] < 0:
        raise ValueError('Window features must be finite and vibration nonnegative')
    if not math.isclose(numbers['temperature_sum']/row['reading_count'],numbers['temperature_mean'],rel_tol=1e-12,abs_tol=1e-12):
        raise ValueError('Window mean disagrees with its absolute sum/count')
    payload = {'machine_id':row['machine_id'],'event_timestamp':end.isoformat(),'created':created.isoformat(),
               'reading_count':row['reading_count'],**numbers}
    semantic = {k:v for k,v in payload.items() if k!='created'}
    canonical = json.dumps(semantic,sort_keys=True,separators=(',',':'),allow_nan=False)
    key = json.dumps([row['machine_id'],end.isoformat()],separators=(',',':'))
    return key,sha256(canonical.encode()).hexdigest(),payload


class WindowOutbox:
    """Journal intent and acknowledgement separately; a pending intent must be reconciled against both stores."""

    def __init__(self,path: Path):
        """Keep intent durability local to this single-writer coursework job."""
        path.parent.mkdir(parents=True,exist_ok=True)
        self.connection=sqlite3.connect(path)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA synchronous=FULL')
        self.connection.execute('CREATE TABLE IF NOT EXISTS intents(key TEXT PRIMARY KEY,semantic_sha256 TEXT NOT NULL,payload TEXT NOT NULL,completed_utc TEXT)')
        self.connection.commit()

    def observe(self,row: dict) -> dict:
        """Preserve the first availability timestamp; reject conflicting immutable feature values before writing."""
        key,semantic,payload=window_payload(row)
        encoded=json.dumps(payload,sort_keys=True,allow_nan=False)
        self.connection.execute('INSERT OR IGNORE INTO intents(key,semantic_sha256,payload) VALUES(?,?,?)',(key,semantic,encoded))
        self.connection.commit()
        previous=self.connection.execute('SELECT semantic_sha256,payload,completed_utc FROM intents WHERE key=?',(key,)).fetchone()
        if previous[0]!=semantic:
            raise ValueError('Conflicting finalized window; refuse both-store mutation')
        return {'key':key,'semantic_sha256':semantic,'payload':json.loads(previous[1]),'completed_utc':previous[2]}

    def acknowledge(self,key: str):
        """Acknowledge only after the caller has verified offline uniqueness and the latest online vector."""
        cursor=self.connection.execute('UPDATE intents SET completed_utc=? WHERE key=? AND completed_utc IS NULL',
                                       (datetime.now(timezone.utc).isoformat(),key))
        if cursor.rowcount!=1:
            raise ValueError('Expected one pending intent to acknowledge')
        self.connection.commit()

    def records(self):
        """Return bounded window metadata for cross-store verification, never raw telemetry history."""
        return [{'key':k,'semantic_sha256':s,'payload':json.loads(p),'completed_utc':t}
                for k,s,p,t in self.connection.execute('SELECT key,semantic_sha256,payload,completed_utc FROM intents ORDER BY key')]

    def close(self):
        """Release the journal after its committed intents remain available for a checked retry."""
        self.connection.close()
