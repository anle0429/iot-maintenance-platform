"""Check cumulative Spark IO accounting and the distinction between retries and completed stages."""

import json
from pathlib import Path
import tempfile
import unittest

from pipelines.ingest_metrics import summarize_events


class IngestMetricTests(unittest.TestCase):
    """Use fixed event-log answers rather than relying on a live Spark deployment."""

    def test_task_metrics_include_actual_attempts_and_both_shuffle_read_locations(self):
        """A failed retry still performed IO, while peak task memory is a maximum rather than a sum."""
        events=[{'Event':'SparkListenerStageCompleted'},
                {'Event':'SparkListenerTaskEnd','Task End Reason':{'Reason':'Success'},'Task Metrics':{
                    'Input Metrics':{'Bytes Read':100},'Output Metrics':{'Bytes Written':50},
                    'Shuffle Write Metrics':{'Shuffle Bytes Written':30},
                    'Shuffle Read Metrics':{'Remote Bytes Read':10,'Local Bytes Read':20},
                    'Disk Bytes Spilled':40,'Memory Bytes Spilled':80,'Peak Execution Memory':200}},
                {'Event':'SparkListenerTaskEnd','Task End Reason':{'Reason':'ExceptionFailure'},'Task Metrics':{
                    'Input Metrics':{'Bytes Read':7},'Disk Bytes Spilled':3,'Peak Execution Memory':150}}]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'events'
            path.write_text(''.join(json.dumps(e)+'\n' for e in events),encoding='utf-8')
            result=summarize_events(path)
        self.assertEqual(result['task_attempts'],2)
        self.assertEqual(result['failed_attempts'],1)
        self.assertEqual(result['stages_completed'],1)
        self.assertEqual(result['input_bytes_read'],107)
        self.assertEqual(result['output_bytes_written'],50)
        self.assertEqual(result['shuffle_bytes_read'],30)
        self.assertEqual(result['shuffle_bytes_written'],30)
        self.assertEqual(result['disk_bytes_spilled'],43)
        self.assertEqual(result['memory_bytes_spilled'],80)
        self.assertEqual(result['max_task_peak_execution_memory'],200)

    def test_non_task_events_do_not_invent_resource_usage(self):
        """An application with only lifecycle events reports zero task work, not missing values as usage."""
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'events'
            path.write_text('{"Event":"SparkListenerApplicationStart"}\n',encoding='utf-8')
            result=summarize_events(path)
        self.assertEqual(result['task_attempts'],0)
        self.assertEqual(result['disk_bytes_spilled'],0)
        self.assertIn('not peak',result['scope'])
        self.assertFalse(result['application_ended'])

    def test_explicit_application_end_is_required_for_complete_metrics(self):
        """Even a valid start-only log is partial evidence until Spark emits its actual end event."""
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'events'
            path.write_text('{"Event":"SparkListenerApplicationStart"}\n{"Event":"SparkListenerApplicationEnd"}\n',encoding='utf-8')
            self.assertTrue(summarize_events(path)['application_ended'])


if __name__=='__main__':
    unittest.main()
