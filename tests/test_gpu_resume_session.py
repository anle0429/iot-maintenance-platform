"""Reject wrong machine/network/session boundaries before a retained GPU recovery can write runtime facts."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import importlib.util, unittest
SPEC = importlib.util.spec_from_file_location('resume_gpu_services', Path(__file__).resolve().parents[1]/'scripts/resume-gpu-services.py')
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)


class SessionBoundaryTests(unittest.TestCase):
    """Check actual-descriptor parsing and independent invalid authority/resource/timing partitions."""
    def setUp(self):
        """Create an isolated descriptor fixture; never use a cloud client or private runtime file."""
        self.now = datetime(2026, 10, 30, 6, tzinfo=timezone.utc)
        self.descriptor = {'name': 'iot-cw-gpu', 'id': '11538226507322059', 'status': 'RUNNING',
            'lastStartTimestamp': '2026-10-30T03:49:37.642Z',
            'scheduling': {'maxRunDuration': {'seconds': '28800'}, 'instanceTerminationAction': 'STOP'},
            'networkInterfaces': [{'networkIP': '10.42.0.7'}]}

    def test_actual_start_is_preserved(self):
        """Use the descriptor's start rather than minting a fresh eight-hour lease from the current clock."""
        result = MODULE.session_from_descriptor(self.descriptor, self.now)
        self.assertEqual(result['last_start_utc'], self.descriptor['lastStartTimestamp'])
        self.assertEqual(result['max_duration_seconds'], 28800)

    def test_reject_wrong_identity_network_and_duration(self):
        """Refuse another machine, public networking, changed STOP policy and stale/future session clocks."""
        variants = []
        for key, value in [('id', '999'), ('status', 'TERMINATED'), ('lastStartTimestamp', '2026-10-29T00:00:00Z'), ('lastStartTimestamp', '2026-10-30T07:00:00Z')]:
            row = deepcopy(self.descriptor); row[key] = value; variants.append(row)
        row = deepcopy(self.descriptor); row['networkInterfaces'][0]['accessConfigs'] = [{'natIP': '203.0.113.5'}]; variants.append(row)
        row = deepcopy(self.descriptor); row['scheduling']['instanceTerminationAction'] = 'DELETE'; variants.append(row)
        row = deepcopy(self.descriptor); row['scheduling']['maxRunDuration']['seconds'] = '57600'; variants.append(row)
        for row in variants:
            with self.subTest(descriptor=row), self.assertRaises(ValueError): MODULE.session_from_descriptor(row, self.now)


if __name__ == '__main__': unittest.main()
