"""Define known-answer streaming deliveries, window truth and an idempotent local audit sink."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from threading import RLock

from generator.config import iso_ts, parse_ts


def millis(value: str) -> int:
    """Convert an aware source timestamp into event-time milliseconds."""
    return int(parse_ts(value).timestamp() * 1000)


def utc_millis(value: int) -> str:
    """Format a Flink millisecond window boundary in UTC."""
    return iso_ts(datetime.fromtimestamp(value / 1000, timezone.utc))


def fixture_phases() -> list[list[dict]]:
    """Create six readings, retries/replays, a 10-minute boundary and two distinct late cases."""
    start = parse_ts("2026-10-01T00:00:00Z")

    def delivery(name, minute, arrived, temperature, machine="P-001", attempt=1):
        """Keep first-arrival payload immutable when emitting a retry envelope."""
        received = iso_ts(start + timedelta(minutes=arrived))
        return {"kind": "telemetry", "delivery_id": f"{name}-{attempt}", "attempt": attempt,
                "published_ts": iso_ts(start + timedelta(minutes=arrived, seconds=2 * (attempt - 1))),
                "event": {"event_id": name, "machine_id": machine,
                          "event_ts": iso_ts(start + timedelta(minutes=minute)),
                          "received_ts": received, "created_ts": received, "schema_version": 1,
                          "temperature_c": temperature, "vibration_rms_mm_s": temperature / 10,
                          "current_a": 5.0, "rpm": 2800.0, "operating_status": "operating"}}

    def tick(number, minute):
        """Advance event time without pretending a control record is machine telemetry."""
        return {"kind": "progress", "marker": number, "event_ts": iso_ts(start + timedelta(minutes=minute))}

    a, b = delivery("A", 1, 1, 10.0), delivery("B", 4, 4, 20.0)
    retry = delivery("B", 4, 4, 20.0, attempt=2)
    other = delivery("M2", 2, 2, 100.0, "P-002")
    boundary = delivery("C", 10, 10, 40.0)
    within = delivery("D", 8, 16, 30.0)
    too_late = delivery("E", 7, 32, 900.0)
    early = [a, other, b, retry]
    first = [{**row, "replay_round": 1} for row in early] + [{**row, "replay_round": 2} for row in early]
    first += [{**boundary, "replay_round": number} for number in (1, 2)] + [tick(1, 11)]
    last = [too_late] + [{**row, "replay_round": 3} for row in [a, other, b, retry, boundary, within, too_late]] + [tick(3, 50)]
    phases = [first, [within, tick(2, 26)], last]
    sequence = 0
    for phase in phases:
        for row in phase:
            row["sequence"] = sequence
            sequence += 1
    return phases


def window_truth(events: list[dict]) -> list[dict]:
    """Compute independent deduplicated tumbling-window totals for the supplied accepted events."""
    unique = {row["event_id"]: row for row in events}
    groups = {}
    for row in unique.values():
        start = millis(row["event_ts"]) // 600_000 * 600_000
        key = (row["machine_id"], start)
        groups.setdefault(key, []).append(row)
    return [{"machine_id": key[0], "window_start": utc_millis(key[1]), "event_timestamp": utc_millis(key[1] + 600_000),
             "reading_count": len(rows), "temperature_sum": sum(r["temperature_c"] for r in rows),
             "temperature_mean": sum(r["temperature_c"] for r in rows) / len(rows),
             "vibration_max": max(r["vibration_rms_mm_s"] for r in rows)}
            for key, rows in sorted(groups.items())]


class StreamStore:
    """Upsert absolute window results and keyed audits; this is not a checkpoint transaction sink."""

    def __init__(self, path: Path):
        """Open a per-scenario SQLite file, retaining state across replay writes."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS audits(identity TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS windows(machine_id TEXT, window_start TEXT, payload TEXT NOT NULL,
                                              PRIMARY KEY(machine_id,window_start));
        """)

    def write(self, record: dict) -> None:
        """Use stable keys rather than incrementing aggregates again when a result is replayed."""
        with self.lock:
            self._write_locked(record)

    def _write_locked(self, record: dict) -> None:
        """Serialize Beam bundle-thread handoffs on the same operator connection."""
        payload = json.dumps(record, sort_keys=True)
        if record["kind"] == "window":
            self.connection.execute("INSERT INTO windows VALUES (?,?,?) ON CONFLICT(machine_id,window_start) DO UPDATE SET payload=excluded.payload",
                                    (record["machine_id"], record["window_start"], payload))
        else:
            if record["kind"] in ("unique", "late"):
                identity = f"{record['kind']}:{record['event']['event_id']}"
            else:
                identity = f"{record['kind']}:{record['sequence']}"
            self.connection.execute("INSERT OR IGNORE INTO audits VALUES (?,?,?)", (identity, record["kind"], payload))
        self.connection.commit()

    def close(self) -> None:
        """Flush pending bounded-job writes and release the database handle."""
        with self.lock:
            self.connection.commit()
            self.connection.close()


def read_store(path: Path) -> dict:
    """Read only committed rows, including progress barriers written by the running Flink job."""
    with closing(sqlite3.connect(path, timeout=30)) as connection:
        audits = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM audits ORDER BY identity")]
        windows = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM windows ORDER BY machine_id,window_start")]
    return {"audits": audits, "windows": windows}


class EventLedger:
    """Retain immutable event IDs on disk so replay remains deduplicated after cache expiry."""

    def __init__(self, path: Path, export_path: Path | None = None):
        """Open a local single-writer event journal independent of the finite Flink cache."""
        self.export_path=export_path
        path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=RLock()
        self.connection=sqlite3.connect(path,timeout=30,check_same_thread=False)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA synchronous=NORMAL')
        self.connection.execute('CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        self.connection.commit()

    def observe(self, event: dict) -> bool:
        """Commit a new reading once, reject conflicts and identify historical retries."""
        canonical=json.dumps(event,sort_keys=True)
        with self.lock:
            old=self.connection.execute('SELECT payload FROM events WHERE event_id=?',(event['event_id'],)).fetchone()
            if old is not None:
                if old[0]!=canonical:
                    raise ValueError(f"conflicting ledger event_id: {event['event_id']}")
                return False
            self.connection.execute('INSERT INTO events VALUES (?,?)',(event['event_id'],canonical))
            self.connection.commit()
            return True

    def flush(self) -> None:
        """Flush any pending work; event writes also commit before returning."""
        with self.lock:
            self.connection.commit()

    def close(self) -> None:
        """Close the journal without deleting the persistent replay protection."""
        with self.lock:
            self.flush()
            if self.export_path is not None:
                self.export_path.parent.mkdir(parents=True,exist_ok=True)
                with closing(sqlite3.connect(self.export_path)) as destination:
                    self.connection.backup(destination)
            self.connection.close()


def volume_store_path(export_path: Path) -> Path:
    """Name a Linux-local database from the unique run/scenario export location."""
    from hashlib import sha256
    return Path('/var/lib/iot-ledgers') / (sha256(export_path.as_posix().encode()).hexdigest()[:24]+'.sqlite')


def export_sqlite(source_path: Path, target_path: Path) -> None:
    """Snapshot committed WAL data after job completion without depending on a UDF close callback."""
    if not source_path.is_file():
        raise FileNotFoundError(f'Missing local result database: {source_path}')
    target_path.parent.mkdir(parents=True,exist_ok=True)
    temporary=target_path.with_suffix('.snapshot.tmp')
    with closing(sqlite3.connect(f'{source_path.as_uri()}?mode=ro',uri=True)) as source:
        with closing(sqlite3.connect(temporary)) as destination:
            source.backup(destination)
    temporary.replace(target_path)
