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


class LedgerBoundaryTests(unittest.TestCase):
    """Check the complete public fact vocabulary, valid extremes and independent durable row counts."""
    def test_request_labels_and_optional_fields_round_trip_without_coercion(self):
        """Every supported role, result, completion reason and policy category remains a bounded scalar."""
        for role in ['retrieval','drift','coordinator','direct','unknown']:
            self.assertEqual(validate_fact('request',fact()|{'agent':role})['agent'],role)
        for result in ['ok','policy_rejected','invalid_request','upstream_error','transport_error','invalid_response','cancelled']:
            self.assertEqual(validate_fact('request',fact()|{'result':result})['result'],result)
        for reason in [None,'stop','length','tool_calls','content_filter','other']:
            self.assertEqual(validate_fact('request',fact()|{'finish_reason':reason})['finish_reason'],reason)
        self.assertIsNone(validate_fact('request',fact()|{'usage':None,'ttft_seconds':None,'streaming':False})['ttft_seconds'])
        self.assertEqual(validate_fact('request',fact()|{'model':'unknown'})['model'],'unknown')
        self.assertEqual(validate_fact('request',fact()|{'policy_categories':['hardware_command','pii_email','pii_phone','secret_literal']})['policy_categories'],
            ['hardware_command','pii_email','pii_phone','secret_literal'])

    def test_request_boundaries_reject_outside_values_and_accept_both_endpoints(self):
        """Finite durations, actual integer HTTP statuses and TTFT ordering have inclusive documented limits."""
        for value in [0,3600]:self.assertEqual(validate_fact('request',fact()|{'duration_seconds':value,'ttft_seconds':value})['duration_seconds'],value)
        for value in [100,599]:self.assertEqual(validate_fact('request',fact()|{'status':value})['status'],value)
        changes=[{'duration_seconds':v} for v in [-.001,3600.001,True,'1',float('inf')]]
        changes += [{'ttft_seconds':v} for v in [-.001,1.501,True,'1',float('nan'),float('inf')]]
        changes += [{'status':v} for v in [99,600,200.0,'200']]
        changes += [{'policy_categories':v} for v in [None,{},[[]],['pii_phone','pii_email']]]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(ValueError):validate_fact('request',fact()|change)
        for key in fact():
            value=fact();value.pop(key)
            with self.subTest(missing=key),self.assertRaises(ValueError):validate_fact('request',value)
        for value in [None,[],1]:
            with self.subTest(value=value),self.assertRaises(ValueError):validate_fact('request',value)

    def test_capacity_and_identity_boundaries_keep_independent_sorted_counts(self):
        """Accept the advertised last valid capacity/ID sizes and verify real rows, not only an insertion return value."""
        with TemporaryDirectory() as directory:
            p=Path(directory)/'events.sqlite'
            for capacity in [0,1000001,True,1.5]:
                with self.subTest(capacity=capacity),self.assertRaises(ValueError):TelemetryLedger(p,capacity)
            store=TelemetryLedger(p,1000000);self.assertEqual(store.size(),0)
            for identity in ['b'*80,'a'*16]:self.assertTrue(store.put('request',identity,fact()))
            self.assertEqual(store.size(),2);self.assertEqual([r['event_id'] for r in store.rows()],['a'*16,'b'*80])
            for identity in ['a'*15,'a'*81,None,True]:
                with self.subTest(identity=identity),self.assertRaises(ValueError):store.put('request',identity,fact())
            self.assertEqual(store.size(),2);store.close();store=TelemetryLedger(p);self.assertEqual(store.size(),2);store.close()

    def test_span_vocabulary_and_status_match_original_graph_facts(self):
        """Keep actual roots, all supported operations/tools and each real OTLP status without inventing success."""
        base={'agent':'unknown','model':'unknown','duration_seconds':0,'usage':None,'operation':'other','tool':'none','error':False,'status_code':0,
            'trace_id':'1'*32,'span_id':'2'*16,'parent_span_id':'','start_ns':0,'end_ns':0}
        for operation in ['invocation','agent','model','tool','transport','other']:
            self.assertEqual(validate_fact('span',base|{'operation':operation})['operation'],operation)
        for tool in ['none','get_machine_features','predict_failure','search_knowledge','assess_feature_drift','delegate_retrieval','delegate_drift']:
            self.assertEqual(validate_fact('span',base|{'tool':tool})['tool'],tool)
        for status in [0,1,2]:self.assertEqual(validate_fact('span',base|{'status_code':status,'error':status==2})['status_code'],status)
        changes=[{'status_code':True},{'status_code':3},{'error':True},{'error':1},{'trace_id':'1'*31},{'span_id':'0'*16},
            {'parent_span_id':'0'*16},{'start_ns':True},{'start_ns':-1},{'end_ns':1},{'duration_seconds':.5}]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(ValueError):validate_fact('span',base|change)
        with self.assertRaises(ValueError):validate_fact('other',base)


if __name__=='__main__':unittest.main()
