"""Persist idempotent window assessments in a bounded local-volume SQLite transaction shared by replicas."""
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import zlib
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
            connection.execute('''CREATE TABLE IF NOT EXISTS archived_windows(
                reference TEXT, id TEXT, request_hash TEXT NOT NULL, response_zlib BLOB NOT NULL,
                PRIMARY KEY(reference,id))''')
            connection.commit()

    def connect(self):
        """Create a thread-owned connection; rollback/close never depend on garbage collection."""
        return sqlite3.connect(self.database, timeout=5)

    def ready(self):
        """Verify the persistent table is readable rather than merely advertising process liveness."""
        with closing(self.connect()) as connection:
            count = connection.execute('SELECT COUNT(*) FROM windows').fetchone()[0]
        return {'ready': True, 'reference_id': self.detector.reference_id, 'stored_windows': count}

    def capacity(self):
        """Distinguish availability of retained replays from capacity for new assessments."""
        with closing(self.connect()) as connection:
            active, archived = connection.execute('''SELECT (SELECT COUNT(*) FROM windows),
                (SELECT COUNT(*) FROM archived_windows)''').fetchone()
        return {'accepts_new_windows': active < self.max_windows, 'active_windows': active,
                'archived_windows': archived, 'max_active_windows': self.max_windows}

    def archive_windows(self, keep_active):
        """Move the oldest assessments atomically and keep their entire replay contract."""
        if type(keep_active) is not int or not 0 <= keep_active < self.max_windows:
            raise ValueError('Keep fewer active windows than the admission limit')
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            count = connection.execute('SELECT COUNT(*) FROM windows').fetchone()[0]
            rows = connection.execute('''SELECT rowid,reference,id,request_hash,response FROM windows
                ORDER BY rowid LIMIT ?''', (max(0, count - keep_active),)).fetchall()
            connection.executemany('INSERT INTO archived_windows VALUES(?,?,?,?)',
                                   ((ref, identity, hash_, zlib.compress(response.encode()))
                                    for _, ref, identity, hash_, response in rows))
            connection.executemany('DELETE FROM windows WHERE rowid=?', ((rowid,) for rowid, *_ in rows))
            return len(rows)

    def compare(self, request):
        """Return byte-equivalent cached results; reject changed data under an existing window ID."""
        window = request.canonical()
        if window['reference_id'] != self.detector.reference_id:
            raise WindowConflict('Reference does not match this deployment')
        identity = (window['reference_id'], window['window_id'])
        digest = sha256(canonical_bytes(window)).hexdigest()
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            previous = connection.execute('''SELECT request_hash,response FROM windows WHERE reference=? AND id=?
                UNION ALL SELECT request_hash,response_zlib FROM archived_windows WHERE reference=? AND id=?''',
                                          (*identity, *identity)).fetchone()
            if previous:
                if previous[0] != digest:
                    raise WindowConflict('Window ID already contains different data')
                response = previous[1]
                return json.loads(zlib.decompress(response) if isinstance(response, bytes) else response)
            if connection.execute('SELECT COUNT(*) FROM windows').fetchone()[0] >= self.max_windows:
                raise StoreFull('Assessment capacity reached; archive with an explicit retention policy')
            result = self.detector.compare(window)
            connection.execute('INSERT INTO windows VALUES(?,?,?,?)', (*identity, digest, canonical_bytes(result).decode()))
            return result


def main():
    """Archive assessments in the existing state volume using its mounted reference."""
    import argparse
    import os

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('--keep-active', type=int, required=True)
    args = parser.parse_args()
    reference = json.loads(Path(os.environ['IOT_DRIFT_REFERENCE']).read_bytes())
    runtime = DriftRuntime(reference, os.environ['IOT_DRIFT_DATABASE'])
    moved = runtime.archive_windows(args.keep_active)
    print(json.dumps({'moved_windows': moved, **runtime.capacity()}))


if __name__ == '__main__':
    main()
