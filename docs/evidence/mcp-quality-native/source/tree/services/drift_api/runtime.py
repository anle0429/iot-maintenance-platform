"""Persist idempotent window assessments in a bounded local-volume SQLite transaction shared by replicas."""
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
from services.drift_api.detector import DriftDetector, canonical_bytes


class WindowConflict(ValueError):
    """Signal an identity collision or request for a different baseline."""


class StoreFull(RuntimeError):
    """Preserve previous assessments when the declared retention capacity has been reached."""


class DriftRuntime:
    """Serialize check-and-insert so retries across workers cannot create extra windows."""
    def __init__(self, bundle, database, max_windows=10000):
        """Initialize the fixed reference and a durable bounded assessment table."""
        if type(max_windows) is not int or max_windows < 1:
            raise ValueError('max_windows must be positive')
        self.detector = DriftDetector(bundle)
        self.database = str(database)
        self.max_windows = max_windows
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS windows(reference TEXT, id TEXT, request_hash TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(reference,id))')
            connection.commit()

    def connect(self):
        """Create a thread-owned connection; rollback/close never depend on garbage collection."""
        return sqlite3.connect(self.database, timeout=5)

    def ready(self):
        """Verify the persistent table is readable rather than merely advertising process liveness."""
        with closing(self.connect()) as connection:
            count = connection.execute('SELECT COUNT(*) FROM windows').fetchone()[0]
        return {'ready': True, 'reference_id': self.detector.reference_id, 'stored_windows': count}

    def compare(self, request):
        """Return byte-equivalent cached results; reject changed data under an existing window ID."""
        window = request.canonical()
        if window['reference_id'] != self.detector.reference_id:
            raise WindowConflict('Reference does not match this deployment')
        identity = (window['reference_id'], window['window_id'])
        digest = sha256(canonical_bytes(window)).hexdigest()
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            previous = connection.execute('SELECT request_hash,response FROM windows WHERE reference=? AND id=?', identity).fetchone()
            if previous:
                if previous[0] != digest:
                    raise WindowConflict('Window ID already contains different data')
                return json.loads(previous[1])
            if connection.execute('SELECT COUNT(*) FROM windows').fetchone()[0] >= self.max_windows:
                raise StoreFull('Assessment capacity reached; archive with an explicit retention policy')
            result = self.detector.compare(window)
            connection.execute('INSERT INTO windows VALUES(?,?,?,?)', (*identity, digest, canonical_bytes(result).decode()))
            return result
