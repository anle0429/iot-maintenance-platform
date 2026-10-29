"""Reconcile metric totals and histogram boundaries with independently known durable facts."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from prometheus_client.parser import text_string_to_metric_families
from llm_observability.ledger import TelemetryLedger
from llm_observability.metrics import render_metrics
from tests.test_llm_telemetry_ledger import fact


def samples(raw):
    """Use the independent Prometheus parser rather than splitting the renderer's own output format."""
    return {(sample.name,tuple(sorted(sample.labels.items()))):sample.value for family in text_string_to_metric_families(raw.decode()) for sample in family.samples}


def sample_value(values,name,**labels):
    """Resolve one exact metric series without matching arbitrary label subsets."""
    return values[(name,tuple(sorted(labels.items())))]


class ObservationMetricsTests(unittest.TestCase):
    """Known outcomes include valid generation, guarded input, missing metadata and independent traced invocations."""
    def test_reported_tokens_ttft_histograms_and_policy_denominators(self):
        """Recompute totals and bucket boundaries while keeping a blocked prompt out of generated-token counts."""
        good=fact();blocked=fact()|{'usage':None,'ttft_seconds':None,'duration_seconds':0.01,'result':'policy_rejected','status':422,'finish_reason':None,'policy_categories':['pii_email']}
        rows=[{'kind':'request','event_id':'a'*32,'facts':good},{'kind':'request','event_id':'b'*32,'facts':blocked}]
        data=samples(render_metrics(rows));labels={'agent':'retrieval','model':good['model']}
        self.assertEqual(sample_value(data,'iot_llm_reported_tokens_total',**labels,type='input'),10)
        self.assertEqual(sample_value(data,'iot_llm_reported_tokens_total',**labels,type='output'),5)
        self.assertEqual(sample_value(data,'iot_llm_reported_tokens_total',**labels,type='total'),15)
        self.assertEqual(sample_value(data,'iot_llm_request_seconds_count',**labels),2)
        self.assertAlmostEqual(sample_value(data,'iot_llm_request_seconds_sum',**labels),1.51)
        self.assertEqual(sample_value(data,'iot_llm_first_output_seconds_count',**labels),1)
        self.assertEqual(sample_value(data,'iot_llm_first_output_seconds_bucket',**labels,le='0.5'),1)
        self.assertEqual(sample_value(data,'iot_llm_first_output_seconds_bucket',**labels,le='0.25'),0)
        self.assertEqual(sample_value(data,'iot_llm_first_output_seconds_bucket',**labels,le='+Inf'),1)
        self.assertEqual(sample_value(data,'iot_llm_policy_checks_total',outcome='allowed'),1)
        self.assertEqual(sample_value(data,'iot_llm_policy_checks_total',outcome='blocked'),1)
        self.assertEqual(sample_value(data,'iot_llm_policy_hits_total',category='pii_email'),1)

    def test_restart_replay_and_input_order_do_not_change_exposition(self):
        """Derive the same metrics after durable replay/reopen rather than persisting a second mutable counter system."""
        with TemporaryDirectory() as directory:
            path=Path(directory)/'events.sqlite';store=TelemetryLedger(path)
            store.put('request','a'*32,fact());store.put('request','b'*32,fact()|{'ttft_seconds':None,'streaming':False})
            before=render_metrics(store.rows());store.close();store=TelemetryLedger(path)
            self.assertFalse(store.put('request','a'*32,fact()));after=render_metrics(list(reversed(store.rows())))
            self.assertEqual(before,after);store.close()

    def test_agent_and_tool_counts_require_real_span_operations(self):
        """Do not call model-turn counts agent invocations or count a transport/root wrapper as a second agent run."""
        span={'agent':'drift','model':fact()['model'],'duration_seconds':0.5,'usage':None,'operation':'agent','tool':'none','error':False,'status_code':1,
              'trace_id':'a'*32,'span_id':'b'*16,'parent_span_id':'','start_ns':1000000000,'end_ns':1500000000}
        rows=[{'kind':'span','event_id':'a'*48,'facts':span},
              {'kind':'span','event_id':'b'*48,'facts':span|{'span_id':'c'*16,'operation':'invocation'}},
              {'kind':'span','event_id':'c'*48,'facts':span|{'span_id':'d'*16,'operation':'tool','tool':'assess_feature_drift','error':True,'status_code':2}}]
        data=samples(render_metrics(rows))
        self.assertEqual(sample_value(data,'iot_agent_invocations_total',agent='drift',result='success'),1)
        self.assertEqual(sample_value(data,'iot_agent_traced_tool_calls_total',agent='drift',tool='assess_feature_drift',result='failure'),1)

    def test_missing_usage_and_invalid_requests_remain_distinguishable(self):
        """Keep failed protocol metadata visible without fabricating zero token/TTFT samples."""
        bad=fact()|{'usage':None,'ttft_seconds':None,'result':'invalid_response'}
        invalid=bad|{'status':400,'result':'invalid_request'}
        rows=[{'kind':'request','event_id':'a'*32,'facts':bad},{'kind':'request','event_id':'b'*32,'facts':invalid}]
        data=samples(render_metrics(rows));self.assertEqual(sample_value(data,'iot_llm_usage_missing_total',agent='retrieval',model=bad['model']),1)
        self.assertEqual(sample_value(data,'iot_llm_policy_checks_total',outcome='allowed'),1)
        self.assertFalse(any(k[0]=='iot_llm_reported_tokens_total' or k[0]=='iot_llm_first_output_seconds_count' for k in data))

    def test_duplicate_or_unreviewed_rows_are_refused(self):
        """Fail visibly on corrupted input instead of producing inflated or free-text metric labels."""
        row={'kind':'request','event_id':'a'*32,'facts':fact()}
        for rows in [[row,row],[row|{'prompt':'private'}],[row|{'facts':fact()|{'agent':'private text'}}]]:
            with self.subTest(rows=len(rows)),self.assertRaises(ValueError):render_metrics(rows)
        self.assertEqual(render_metrics([]),b'\n')


if __name__=='__main__':unittest.main()
