"""Check stream fixture truth, time boundaries and local sink idempotency independently of Flink."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from pipelines.stream_contract import EventLedger, StreamStore, export_sqlite, fixture_phases, millis, read_store, window_truth


class StreamContractTests(unittest.TestCase):
    """Protect meaningful known-answer cases before exercising them through Kafka/Flink."""

    def test_fixture_has_exact_unique_retry_replay_counts(self):
        """Nineteen telemetry deliveries contain exactly six immutable readings and thirteen extras."""
        rows = [row for phase in fixture_phases() for row in phase if row['kind'] == 'telemetry']
        by_id = {}
        for row in rows:
            by_id.setdefault(row['event']['event_id'], set()).add(json.dumps(row['event'], sort_keys=True))
        self.assertEqual(len(rows), 19)
        self.assertEqual(len(by_id), 6)
        self.assertTrue(all(len(values) == 1 for values in by_id.values()))

    def test_half_open_window_boundary_and_machine_partition(self):
        """Reading C at 00:10 belongs to the next window; another machine never mixes into P-001."""
        events = [row['event'] for phase in fixture_phases() for row in phase
                  if row['kind'] == 'telemetry' and row['event']['event_id'] != 'E']
        windows = window_truth(events)
        self.assertEqual([(r['machine_id'],r['reading_count'],r['temperature_mean']) for r in windows],
                         [('P-001',3,20.0),('P-001',1,40.0),('P-002',1,100.0)])

    def test_two_late_partitions_are_distinct(self):
        """D arrives eight minutes late before corrected closure; E arrives after that closure."""
        phases = fixture_phases()
        within, beyond = phases[1][0], phases[2][0]
        self.assertEqual(millis(within['published_ts']) - millis(within['event']['event_ts']), 480000)
        self.assertGreater(millis(beyond['published_ts']) - millis(beyond['event']['event_ts']), 15*60000)
        self.assertEqual(phases[1][-1]['event_ts'], '2026-10-01T00:26:00Z')

    def test_sink_replay_does_not_increment_window(self):
        """Repeating an absolute window and audit write preserves both values and key counts."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'sink.sqlite'
            store = StreamStore(path)
            record = {'kind':'window','machine_id':'P-001','window_start':'2026-10-01T00:00:00Z','reading_count':3}
            audit = {**fixture_phases()[0][0], 'kind':'unique'}
            store.write(record)
            store.write(audit)
            first = read_store(path)
            store.write(record)
            store.write(audit)
            self.assertEqual(read_store(path),first)
            store.close()

    def test_sink_updates_one_key_without_touching_another_machine(self):
        """A replacement result updates its own window, leaving another machine intact."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'sink.sqlite'
            store = StreamStore(path)
            for machine,count in [('P-001',2),('P-002',5),('P-001',3)]:
                store.write({'kind':'window','machine_id':machine,'window_start':'start','reading_count':count})
            self.assertEqual([r['reading_count'] for r in read_store(path)['windows']], [3,5])
            store.close()

    def test_sink_accepts_serialized_bundle_thread_handoffs(self):
        """Beam can open an operator on one thread and process subsequent bundles on another."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'sink.sqlite'
            store = StreamStore(path)
            with ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(store.write,[{'kind':'window','machine_id':f'P-{i}','window_start':'start','reading_count':1} for i in range(10)]))
            self.assertEqual(len(read_store(path)['windows']),10)
            store.close()

    def test_durable_ledger_rejects_replay_after_reopening(self):
        """Event identity persists after an in-memory cache or process has gone away."""
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ledger.sqlite'
            event=fixture_phases()[0][0]['event']
            ledger=EventLedger(path)
            self.assertTrue(ledger.observe(event))
            ledger.close()
            reopened=EventLedger(path)
            self.assertFalse(reopened.observe(event))
            reopened.close()

    def test_durable_ledger_rejects_conflicting_payload(self):
        """Persistent replay checks must not silently hide a changed sensor value under an old ID."""
        with tempfile.TemporaryDirectory() as directory:
            ledger=EventLedger(Path(directory)/'ledger.sqlite')
            event=fixture_phases()[0][0]['event']
            ledger.observe(event)
            with self.assertRaisesRegex(ValueError,'conflicting'):
                ledger.observe({**event,'temperature_c':999.0})
            ledger.close()

    def test_ledger_exports_a_readable_snapshot_on_close(self):
        """A journal on Docker-local storage can export its exact event keys for host verification."""
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ledger.sqlite'
            exported=Path(directory)/'export.sqlite'
            ledger=EventLedger(path,exported)
            event=fixture_phases()[0][0]['event']
            ledger.observe(event)
            ledger.close()
            snapshot=EventLedger(exported)
            self.assertFalse(snapshot.observe(event))
            snapshot.close()

    def test_ledger_write_commits_without_close_or_timer(self):
        """Each event remains visible when a bounded-job UDF close callback has not run."""
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ledger.sqlite'
            ledger=EventLedger(path)
            event=fixture_phases()[0][0]['event']
            ledger.observe(event)
            reader=EventLedger(path)
            self.assertFalse(reader.observe(event))
            reader.close()
            ledger.close()

    def test_post_job_snapshot_includes_the_final_output_without_udf_close(self):
        """A partial final batch must not disappear when the result operator is still open."""
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'sink.sqlite'
            target=Path(directory)/'snapshot.sqlite'
            store=StreamStore(source)
            last={'kind':'window','machine_id':'P-001','window_start':'end','reading_count':7}
            store.write(last)
            export_sqlite(source.resolve(),target)
            self.assertEqual(read_store(target)['windows'],[last])
            store.close()


if __name__ == '__main__':
    unittest.main()
