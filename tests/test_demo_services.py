"""Protect the approved VM/session boundary and secret-free token-path discovery used by demo recovery."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('demo_services', ROOT / 'scripts/resume-demo-services.py')
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)


class DemoServiceGuards(unittest.TestCase):
    """Use known timestamps and isolated files instead of invoking any cloud or service mutation in tests."""
    def setUp(self):
        """Create a minimal descriptor for exactly the authorized lab shape and8hourSTOP."""
        self.descriptor = {'name': 'iot-cw-vm', 'status': 'RUNNING', 'machineType': 'projects/sample/zones/us-central1-a/machineTypes/e2-highmem-8',
            'disks': [{}, {}, {}], 'lastStartTimestamp': '2026-10-28T23:26:45+00:00',
            'scheduling': {'maxRunDuration': {'seconds': '28800'}, 'instanceTerminationAction': 'STOP'}}

    def test_session_boundary_and_shape(self):
        """A10minute-old session has28,080seconds available after reserve; other shapes and deadlines fail."""
        now = datetime(2026, 10, 28, 23, 36, 45, tzinfo=timezone.utc)
        self.assertEqual(MODULE.session_deadline(self.descriptor, now), 28080)
        for field, value in [('status', 'TERMINATED'), ('machineType', 'e2-highmem-16'), ('disks', [{}, {}])]:
            changed = deepcopy(self.descriptor); changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): MODULE.session_deadline(changed, now)
        for when in [datetime(2026, 10, 28, 23, 26, 44, tzinfo=timezone.utc), datetime(2026, 10, 29, 6, 46, 45, tzinfo=timezone.utc)]:
            with self.subTest(clock=when), self.assertRaises(ValueError): MODULE.session_deadline(self.descriptor, when)
        changed = deepcopy(self.descriptor); changed['scheduling']['instanceTerminationAction'] = 'DELETE'
        with self.assertRaises(ValueError): MODULE.session_deadline(changed, now)

    def test_old_guard_is_backed_up_and_equal_session_is_idempotent(self):
        """Derive the actual next-day deadline, preserve previous bytes, and reject a fabricated longer same-session expiry."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); guard = root / 'session.json'
            original = b'{"last_start_utc":"2026-10-28T15:13:58+00:00","auto_stop_utc":"2026-10-28T23:13:58+00:00"}\n'
            guard.write_bytes(original); first = MODULE.bind_session(guard, root, self.descriptor)
            self.assertEqual(first['auto_stop_utc'], '2026-10-29T07:26:45+00:00')
            self.assertEqual((root / 'previous-session.json').read_bytes(), original)
            self.assertEqual(MODULE.bind_session(guard, root, self.descriptor), first)
            bad = dict(first); bad['auto_stop_utc'] = '2026-11-01T00:00:00+00:00'; guard.write_text(json.dumps(bad))
            with self.assertRaises(ValueError): MODULE.bind_session(guard, root, self.descriptor)

    def test_token_values_are_not_returned_as_file_paths(self):
        """Only explicit authorization-file fields are selected; an inline credential is not exported."""
        config = {'scrape_configs': [{'authorization': {'credentials_file': '/runtime/api.jwt', 'credentials': 'must-not-be-returned'}}]}
        self.assertEqual(MODULE.token_files(config), {'/runtime/api.jwt'})


if __name__ == '__main__':
    unittest.main()
