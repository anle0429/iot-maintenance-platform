"""Test the snapshot grain against timezone aliases, separate times and invalid identities."""
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import unittest
from ml.snapshot import snapshot_id


class SnapshotTest(unittest.TestCase):
    """Protect label joins from machine-only keys and unstable local-time serialization."""
    def test_canonical_known_answer_and_timezone_alias(self):
        instant=datetime(2026,10,1,tzinfo=timezone.utc)
        expected=sha256(b'P-001|2026-10-01T00:00:00.000000Z').hexdigest()
        self.assertEqual(snapshot_id('P-001',instant),expected)
        self.assertEqual(snapshot_id('P-001',instant.astimezone(timezone(timedelta(hours=7)))),expected)

    def test_separate_times_machines_and_invalid_input(self):
        instant=datetime(2026,10,1,tzinfo=timezone.utc)
        self.assertEqual(len({snapshot_id(m,t) for m in ['P-001','P-002'] for t in [instant,instant+timedelta(microseconds=1)]}),4)
        for machine,t in [('P-1',instant),('P-001|',instant),('P-001',instant.replace(tzinfo=None)),('P-001','2026-10-01')]:
            with self.assertRaises(ValueError):snapshot_id(machine,t)


if __name__=='__main__':unittest.main()
