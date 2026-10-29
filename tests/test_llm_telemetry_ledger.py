"""Verify durable telemetry replay, privacy admission, real concurrency and storage boundaries using temporary databases."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from llm_observability.contracts import MODEL
from llm_observability.ledger import TelemetryLedger,validate_fact


def fact():
    """Return one safe measured model-request fact with an independently known token total."""
    return {'agent':'retrieval','model':MODEL,'duration_seconds':1.5,'ttft_seconds':0.5,'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15},
            'status':200,'result':'ok','streaming':True,'finish_reason':'stop','policy_categories':[]}


class TelemetryLedgerTests(unittest.TestCase):
    """Use actual SQLite commits/reopens rather than an in-memory implementation mirror."""
    def test_replay_survives_restart_and_conflicts_preserve_the_original(self):
        """An identical trace/request cannot inflate counters after reopening the same owned database."""
        with TemporaryDirectory() as directory:
            path=Path(directory)/'events.sqlite';store=TelemetryLedger(path,2)
            self.assertTrue(store.put('request','a'*32,fact()));self.assertFalse(store.put('request','a'*32,fact()));store.close()
            store=TelemetryLedger(path,2);self.assertFalse(store.put('request','a'*32,fact()))
            with self.assertRaisesRegex(ValueError,'Conflicting'):store.put('request','a'*32,fact()|{'duration_seconds':2})
            self.assertEqual(store.rows(),[{'kind':'request','event_id':'a'*32,'facts':fact()}]);store.close()

    def test_concurrent_duplicate_delivery_is_committed_once(self):
        """Threaded HTTP/trace retries must produce one durable record, not one per retry."""
        with TemporaryDirectory() as directory:
            store=TelemetryLedger(Path(directory)/'events.sqlite')
            with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(lambda _:store.put('request','b'*32,fact()),range(32)))
            self.assertEqual(sum(results),1);self.assertEqual(len(store.rows()),1);store.close()

    def test_capacity_refuses_new_events_but_still_accepts_known_replay(self):
        """A full ledger must be visible and preserve its receipts instead of silently evicting or double counting."""
        with TemporaryDirectory() as directory:
            store=TelemetryLedger(Path(directory)/'events.sqlite',1);store.put('request','a'*32,fact())
            with self.assertRaisesRegex(ValueError,'admission'):store.put('request','b'*32,fact())
            self.assertFalse(store.put('request','a'*32,fact()));self.assertEqual(len(store.rows()),1);store.close()

    def test_unreviewed_text_or_nested_usage_fields_are_never_persisted(self):
        """Reject prompt/output fields even when hidden inside otherwise plausible provider usage."""
        with TemporaryDirectory() as directory:
            store=TelemetryLedger(Path(directory)/'events.sqlite')
            for value in [fact()|{'prompt':'private text'},fact()|{'usage':fact()['usage']|{'message':'private text'}},
                          fact()|{'policy_categories':['unbounded-category']},fact()|{'agent':'unknown arbitrary prompt'},fact()|{'model':'private model text'}]:
                with self.subTest(keys=sorted(value)),self.assertRaises(ValueError):store.put('request','a'*32,value)
            self.assertEqual(store.rows(),[]);store.close()

    def test_invalid_request_partition_and_event_ids_are_rejected(self):
        """Keep labels, integer status, finite timing and nonstreaming TTFT semantics bounded."""
        examples=[fact()|{'status':True},fact()|{'result':'arbitrary'},fact()|{'streaming':1},fact()|{'duration_seconds':float('nan')},
                  fact()|{'ttft_seconds':2},fact()|{'streaming':False},fact()|{'finish_reason':'arbitrary'},fact()|{'policy_categories':['pii_email','pii_email']}]
        for value in examples:
            with self.subTest(value=repr(value)),self.assertRaises(ValueError):validate_fact('request',value)
        with TemporaryDirectory() as directory:
            store=TelemetryLedger(Path(directory)/'events.sqlite')
            for identity in ['x','../path','z'*32]:
                with self.subTest(identity=identity),self.assertRaises(ValueError):store.put('request',identity,fact())
            store.close()

    def test_span_identity_parentage_and_duration_are_checked(self):
        """Only valid original span relationships and timestamp-derived durations can become invocation/tool metrics."""
        span={'agent':'drift','model':MODEL,'duration_seconds':0.5,'usage':None,'operation':'tool','tool':'assess_feature_drift','error':False,'status_code':1,
              'trace_id':'a'*32,'span_id':'b'*16,'parent_span_id':'c'*16,'start_ns':1000000000,'end_ns':1500000000}
        self.assertEqual(validate_fact('span',span),span)
        for change in [{'trace_id':'0'*32},{'parent_span_id':'bad'},{'operation':'private action'},{'duration_seconds':0.6},{'end_ns':0},{'tool':'private text'}]:
            with self.subTest(change=change),self.assertRaises(ValueError):validate_fact('span',span|change)


if __name__=='__main__':unittest.main()
