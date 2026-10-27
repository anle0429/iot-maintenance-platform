"""Check window identity, timestamp boundaries and durable retry behavior independent of a Feast provider."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from feature_store.outbox import WindowOutbox, window_payload


class WindowOutboxTests(unittest.TestCase):
    """Protect business invariants before testing actual native store writes in a bounded container."""

    def setUp(self):
        """Use a new journal and an independently known absolute window aggregate."""
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'outbox.sqlite'
        self.row={'kind':'window','machine_id':'A','window_start':'2026-10-01T00:00:00Z',
                  'event_timestamp':'2026-10-01T00:10:00Z','created':'2026-10-27T06:00:00Z',
                  'reading_count':2,'temperature_sum':30.0,'temperature_mean':15.0,'vibration_max':2.0}

    def tearDown(self):
        """Keep each scenario isolated from every other journal or runtime."""
        self.temp.cleanup()

    def test_pending_intent_survives_restart_and_replay(self):
        """A lost acknowledgement must retain the original intent without adding a second row."""
        box=WindowOutbox(self.path);first=box.observe(self.row);box.close()
        box=WindowOutbox(self.path)
        try:
            replay={**self.row,'created':'2026-10-27T06:01:00Z'}
            self.assertEqual(box.observe(replay),first)
            self.assertEqual(len(box.records()),1)
            box.acknowledge(first['key'])
            self.assertIsNotNone(box.observe(replay)['completed_utc'])
            self.assertEqual(box.records()[0]['payload']['created'],'2026-10-27T06:00:00+00:00')
        finally:box.close()

    def test_conflicting_finalized_value_does_not_change_original(self):
        """Corrections require an explicit new policy/version rather than silently mutating a closed window."""
        box=WindowOutbox(self.path)
        try:
            original=box.observe(self.row)
            with self.assertRaisesRegex(ValueError,'Conflicting finalized window'):
                box.observe({**self.row,'vibration_max':999.0})
            self.assertEqual(box.observe(self.row),original)
            self.assertEqual(len(box.records()),1)
        finally:box.close()

    def test_entity_and_window_boundaries_are_distinct_keys(self):
        """A second machine or the next exact window must remain an independent feature row."""
        rows=[self.row,{**self.row,'machine_id':'B'},
              {**self.row,'window_start':'2026-10-01T00:10:00Z','event_timestamp':'2026-10-01T00:20:00Z'}]
        self.assertEqual(len({window_payload(r)[0] for r in rows}),3)

    def test_invalid_window_and_nonfinite_aggregate_are_rejected(self):
        """Reject misalignment, time-zone ambiguity and inconsistent count/mean before either store is touched."""
        patches=[{'reading_count':0},{'reading_count':True},{'temperature_mean':16.0},
                 {'vibration_max':float('inf')},{'temperature_sum':float('nan')},
                 {'event_timestamp':'2026-10-01T00:10:00.000001Z'},
                 {'window_start':'2026-10-01T00:00:00.000001Z','event_timestamp':'2026-10-01T00:10:00.000001Z'},
                 {'event_timestamp':'2026-10-01T00:10:00'}, {'created':'2026-10-27T06:00:00'}, {'machine_id':''}]
        for patch in patches:
            with self.subTest(patch=patch), self.assertRaises(ValueError):window_payload({**deepcopy(self.row),**patch})


if __name__=='__main__':unittest.main()
