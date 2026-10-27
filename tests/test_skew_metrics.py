"""Prove grouped task metrics retain failed work, reused-stage attribution and meaningful nonempty-task durations."""

import json
from pathlib import Path
import tempfile
import unittest

from pipelines.skew_metrics import summarize


class SkewMetricTests(unittest.TestCase):
    """Use event answers known independently from the event parser, without needing a live Spark context."""

    def summary(self, events):
        """Write a small real-format event stream and return its parsed statistics."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events'
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            return summarize(path)

    def test_groups_retries_empty_tasks_and_shared_ancestors(self):
        """Empty tasks must not disguise skew, and shared ancestors must not be called disjoint group work."""
        events = [
            {'Event': 'SparkListenerJobStart', 'Job ID': 0, 'Stage IDs': [0, 1], 'Properties': {'spark.jobGroup.id': 'baseline-0'}},
            {'Event': 'SparkListenerTaskEnd', 'Stage ID': 0, 'Task Info': {'Index': 0, 'Launch Time': 0, 'Finish Time': 10},
             'Task End Reason': {'Reason': 'Success'}, 'Task Metrics': {'Input Metrics': {'Bytes Read': 100}, 'Shuffle Write Metrics': {'Shuffle Bytes Written': 70}}},
            {'Event': 'SparkListenerTaskEnd', 'Stage ID': 1, 'Task Info': {'Index': 0, 'Launch Time': 20, 'Finish Time': 50},
             'Task End Reason': {'Reason': 'Success'}, 'Task Metrics': {'Shuffle Read Metrics': {'Local Bytes Read': 50, 'Remote Bytes Read': 20}, 'Disk Bytes Spilled': 40}},
            {'Event': 'SparkListenerTaskEnd', 'Stage ID': 1, 'Task Info': {'Index': 1, 'Attempt': 1, 'Launch Time': 20, 'Finish Time': 22},
             'Task End Reason': {'Reason': 'ExceptionFailure'}, 'Task Metrics': {}},
            {'Event': 'SparkListenerJobStart', 'Job ID': 1, 'Stage IDs': [0, 2], 'Properties': {'spark.jobGroup.id': 'broadcast-0'}},
            {'Event': 'SparkListenerTaskEnd', 'Stage ID': 2, 'Task Info': {'Index': 0, 'Launch Time': 60, 'Finish Time': 68},
             'Task End Reason': {'Reason': 'Success'}, 'Task Metrics': {'Input Metrics': {'Bytes Read': 50}}},
            {'Event': 'SparkListenerApplicationEnd'}]
        result = self.summary(events)
        self.assertTrue(result['application_ended'])
        self.assertEqual(result['groups']['baseline-0']['totals']['failed_attempts'], 1)
        self.assertEqual(result['groups']['baseline-0']['totals']['shuffle_read_bytes'], 70)
        self.assertEqual(result['groups']['baseline-0']['totals']['disk_spill_bytes'], 40)
        self.assertEqual(result['stages']['1']['duration_ms']['median'], 16)
        self.assertEqual(result['stages']['1']['duration_ms']['nonempty_median'], 30)
        self.assertEqual(result['stages']['0']['groups'], ['baseline-0', 'broadcast-0'])
        self.assertEqual(result['groups']['broadcast-0']['totals']['input_bytes'], 150)
        self.assertIn('do not sum groups', result['scope'])

    def test_incomplete_event_log_is_explicit(self):
        """A start-only file does not become proof of a successfully completed application."""
        result = self.summary([{'Event': 'SparkListenerApplicationStart'}])
        self.assertFalse(result['application_ended'])
        self.assertEqual(result['groups'], {})

    def test_lifecycle_or_ungrouped_jobs_do_not_invent_measurements(self):
        """Fixture jobs without a measured group must not appear as a successful baseline/broadcast pair."""
        result = self.summary([{'Event': 'SparkListenerJobStart', 'Job ID': 3, 'Stage IDs': [], 'Properties': None},
                               {'Event': 'SparkListenerApplicationEnd'}])
        self.assertTrue(result['application_ended'])
        self.assertEqual(result['groups'], {})


if __name__ == '__main__':
    unittest.main()
