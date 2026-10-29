"""Check stream fragmentation, durable replay and trace privacy across generated input families."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from hypothesis import given,settings,strategies as st
from llm_observability.contracts import StreamObservation
from llm_observability.ledger import TelemetryLedger
from llm_observability.metrics import render_metrics
from llm_observability.private_traces import PrivateTraceProcessor
from tests.test_llm_observation_contracts import event
from tests.test_llm_telemetry_ledger import fact
from tests.test_llm_private_traces import batch
from tests.test_llm_observation_metrics import samples,sample_value


class ObservationProperties(unittest.TestCase):
    """Express user-facing measurement invariants rather than duplicating the parser or SQLite implementation."""
    @settings(max_examples=36,deadline=None,derandomize=True)
    @given(st.text(alphabet=st.characters(blacklist_categories=('Cs',)),min_size=1,max_size=40),
           st.lists(st.integers(min_value=1,max_value=64),min_size=1,max_size=24),st.integers(min_value=0,max_value=1000))
    def test_transport_fragmentation_cannot_change_reported_usage(self,text,sizes,prompt_tokens):
        """Any valid Unicode output and chunk partition keeps the independently declared usage and first-output semantics."""
        usage={'prompt_tokens':prompt_tokens,'completion_tokens':7,'total_tokens':prompt_tokens+7}
        raw=event({'choices':[{'delta':{'content':text},'finish_reason':'stop'}]})+event({'usage':usage})+b'data: [DONE]\n\n'
        observer=StreamObservation();position=0;index=0
        while position<len(raw):
            size=sizes[index%len(sizes)];observer.feed(raw[position:position+size],.5);position+=size;index+=1
        result=observer.finish(1);self.assertTrue(result['complete']);self.assertEqual(result['usage'],usage);self.assertEqual(result['ttft_seconds'],.5)

    @settings(max_examples=24,deadline=None,derandomize=True)
    @given(st.lists(st.integers(min_value=0,max_value=20),min_size=1,max_size=32))
    def test_replaying_any_identity_order_keeps_unique_request_counts(self,identifiers):
        """A repeated delivery in any order cannot inflate a persisted generation count."""
        with TemporaryDirectory() as directory:
            store=TelemetryLedger(Path(directory)/'events.sqlite')
            for identifier in identifiers:
                usage={'prompt_tokens':identifier,'completion_tokens':1,'total_tokens':identifier+1}
                store.put('request',format(identifier,'032x'),fact()|{'usage':usage})
            values=samples(render_metrics(store.rows()));count=sample_value(values,'iot_llm_request_seconds_count',agent='retrieval',model=fact()['model'])
            self.assertEqual(count,len(set(identifiers)));self.assertEqual(store.size(),len(set(identifiers)));store.close()

    @settings(max_examples=36,deadline=None,derandomize=True)
    @given(st.text(alphabet=st.characters(blacklist_categories=('Cs',)),max_size=60))
    def test_unapproved_text_never_survives_otlp_projection(self,text):
        """Generate arbitrary private Unicode metadata and preserve only the original graph identities."""
        request,span=batch();sentinel='SENSITIVE_TEST_'+text;span.attributes.add(key='user.arbitrary').value.string_value=sentinel
        raw=request.SerializeToString();self.assertIn(b'SENSITIVE_TEST_',raw)
        result=PrivateTraceProcessor().project(raw);self.assertNotIn(b'SENSITIVE_TEST_',result['payload'])
        self.assertEqual(result['spans'][0][1]['trace_id'],span.trace_id.hex());self.assertEqual(result['spans'][0][1]['span_id'],span.span_id.hex())

    @settings(max_examples=36,deadline=None,derandomize=True)
    @given(st.sampled_from(['gen_ai.operation.name','gen_ai.agent.name','kagent.app_name','gen_ai.tool.name','gen_ai.request.model','gen_ai.usage.input_tokens','gen_ai.task.id']),
           st.sampled_from(['array','map','bytes']))
    def test_composite_metadata_cannot_escape_projection_or_crash_classification(self,key,kind):
        """A valid protobuf envelope with an unapproved value type is safely projected onto bounded unknown identities."""
        request,span=batch();span.name='HTTP request';del span.attributes[:];value=span.attributes.add(key=key).value
        if kind=='array':value.array_value.values.add().string_value='PRIVATE_TYPED_VALUE'
        elif kind=='map':value.kvlist_value.values.add(key='private').value.string_value='PRIVATE_TYPED_VALUE'
        else:value.bytes_value=b'PRIVATE_TYPED_VALUE'
        result=PrivateTraceProcessor().project(request.SerializeToString())
        self.assertNotIn(b'PRIVATE_TYPED_VALUE',result['payload']);self.assertEqual(result['received_spans'],1)
        self.assertEqual(result['spans'][0][1]['trace_id'],span.trace_id.hex())


if __name__=='__main__':unittest.main()
