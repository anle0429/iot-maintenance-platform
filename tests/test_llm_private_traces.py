"""Verify native OTLP graph preservation and an adversarial privacy projection before exporting or deriving counters."""
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
import unittest
from llm_observability.private_traces import PrivateTraceProcessor,attributes,approved


def batch():
    """Construct an actual protobuf batch with known spans and deliberately sensitive fields in every text location."""
    request=ExportTraceServiceRequest();resource=request.resource_spans.add();resource.schema_url='https://secret@example.invalid/private'
    for key,value in [('service.name','iot_retrieval'),('service.namespace','iot_agents'),('user.id','SECRET_USER')]:
        resource.resource.attributes.add(key=key).value.string_value=value
    scope=resource.scope_spans.add();scope.scope.name='SECRET_SCOPE';scope.scope.attributes.add(key='private').value.string_value='SECRET_SCOPE_ATTRIBUTE'
    span=scope.spans.add(trace_id=b'a'*16,span_id=b'b'*8,parent_span_id=b'c'*8,start_time_unix_nano=1000000000,end_time_unix_nano=1500000000,name='execute_tool SECRET_NAME',trace_state='private=SECRET_STATE')
    span.status.code=2;span.status.message='SECRET_ERROR';span.events.add(name='SECRET_EVENT');span.links.add(trace_id=b'd'*16,span_id=b'e'*8).attributes.add(key='private').value.string_value='SECRET_LINK'
    for key,value in [('gen_ai.operation.name','execute_tool'),('gen_ai.tool.name','predict_failure'),('kagent.user_id','SECRET_USER'),('gcp.vertex.agent.llm_request','SECRET_PROMPT')]:
        span.attributes.add(key=key).value.string_value=value
    return request,span


class PrivateTraceTests(unittest.TestCase):
    """Use real protobuf serialization and decode the independent output to inspect every protected relationship."""
    def test_sensitive_fields_are_removed_but_actual_parentage_times_and_status_remain(self):
        """Strip prompt/error/event/link/scope/user information without inventing a new trace graph."""
        request,span=batch();raw=request.SerializeToString();result=PrivateTraceProcessor().project(raw)
        self.assertNotIn(b'SECRET',result['payload']);self.assertNotIn(b'example.invalid',result['payload'])
        projected=ExportTraceServiceRequest.FromString(result['payload']);actual=projected.resource_spans[0].scope_spans[0].spans[0]
        self.assertEqual(actual.trace_id,span.trace_id);self.assertEqual(actual.span_id,span.span_id);self.assertEqual(actual.parent_span_id,span.parent_span_id)
        self.assertEqual(actual.start_time_unix_nano,span.start_time_unix_nano);self.assertEqual(actual.end_time_unix_nano,span.end_time_unix_nano)
        self.assertEqual(actual.status.code,2);self.assertEqual(len(actual.events),0);self.assertEqual(len(actual.links),0)
        self.assertEqual(result['spans'][0][1]['agent'],'retrieval');self.assertEqual(result['spans'][0][1]['tool'],'predict_failure')
        self.assertEqual(result['spans'][0][1]['operation'],'tool');self.assertTrue(result['spans'][0][1]['error'])
        self.assertEqual(request.SerializeToString(),raw)

    def test_token_aliases_are_consistent_and_totals_come_from_reported_counts(self):
        """Keep actual typed counts and reject incompatible aliases instead of selecting a convenient one."""
        request,span=batch()
        for key,value in [('gen_ai.usage.input_tokens',11),('gen_ai.usage.output_tokens',7)]:span.attributes.add(key=key).value.int_value=value
        result=PrivateTraceProcessor().project(request.SerializeToString());self.assertEqual(result['spans'][0][1]['usage'],{'prompt_tokens':11,'completion_tokens':7,'total_tokens':18})
        span.attributes.add(key='gen_ai.usage.prompt_tokens').value.int_value=12
        with self.assertRaises(ValueError):PrivateTraceProcessor().project(request.SerializeToString())

    def test_native_executor_root_is_counted_once_without_nested_sdk_double_counting(self):
        """Use the pinned Go executor root as the call identity, keeping SDK agent segments separate."""
        for name,operation,wanted in [('invoke_agent iot_retrieval','invoke_agent','invocation'),('generate_content','chat','model'),('invocation',None,'agent'),('HTTP POST',None,'other')]:
            with self.subTest(name=name):
                request,span=batch();span.name=name;del span.attributes[:]
                if operation:span.attributes.add(key='gen_ai.operation.name').value.string_value=operation
                self.assertEqual(PrivateTraceProcessor().project(request.SerializeToString())['spans'][0][1]['operation'],wanted)

    def test_invalid_protobuf_empty_batches_and_bad_identities_fail_visibly(self):
        """Reject malformed source data before it can be exported or counted."""
        for raw in [b'',b'not protobuf',b'x'*(2*1024**2+1),ExportTraceServiceRequest().SerializeToString()]:
            with self.subTest(size=len(raw)),self.assertRaises(ValueError):PrivateTraceProcessor().project(raw)
        for key,value in [('trace_id',b'a'),('span_id',b'\0'*8),('parent_span_id',b'z'),('end_time_unix_nano',0)]:
            request,span=batch();setattr(span,key,value)
            with self.subTest(key=key),self.assertRaises(ValueError):PrivateTraceProcessor().project(request.SerializeToString())

    def test_conflicting_role_and_repeated_keys_are_not_silently_reclassified(self):
        """Keep misleading identity or duplicate-key exports out of agent/tool counters."""
        request,span=batch();span.attributes.add(key='kagent.app_name').value.string_value='iot_drift'
        with self.assertRaises(ValueError):PrivateTraceProcessor().project(request.SerializeToString())
        request,span=batch();span.attributes.add(key='gen_ai.tool.name').value.string_value='get_machine_features'
        with self.assertRaises(ValueError):PrivateTraceProcessor().project(request.SerializeToString())

    def test_attribute_allowlist_rejects_free_text_and_boolean_token_counts(self):
        """Never turn user-provided strings or booleans into public resource names or usage numbers."""
        for key,value,resource in [('user.id','private',False),('service.name','private-user@example.com',True),
                                   ('gen_ai.usage.input_tokens',True,False),('gen_ai.tool.name','arbitrary text',False),
                                   ('gen_ai.task.id','arbitrary text',False)]:
            with self.subTest(key=key):self.assertFalse(approved(key,value,resource))
        self.assertFalse(approved('service.namespace',[],True))

    def test_unknown_wire_fields_cannot_bypass_the_privacy_projection(self):
        """Discard future/unknown protobuf data instead of relaying bytes the processor has never reviewed."""
        request,_=batch();raw=request.SerializeToString()+b'\xfa\x07\x0cSECRET_EXTRA'
        self.assertNotIn(b'SECRET',PrivateTraceProcessor().project(raw)['payload'])

    def test_remote_agent_target_can_differ_from_the_emitting_service(self):
        """A coordinator's client/delegation span may name retrieval as its target without becoming a retrieval execution."""
        request,span=batch();resource=request.resource_spans[0].resource
        next(a for a in resource.attributes if a.key=='service.name').value.string_value='iot_coordinator'
        del span.attributes[:]
        span.attributes.add(key='gen_ai.agent.name').value.string_value='iot_retrieval'
        span.attributes.add(key='gen_ai.tool.name').value.string_value='iot_agents__NS__iot_retrieval'
        span.attributes.add(key='gen_ai.operation.name').value.string_value='execute_tool'
        actual=PrivateTraceProcessor().project(request.SerializeToString())['spans'][0][1]
        self.assertEqual(actual['agent'],'coordinator');self.assertEqual(actual['tool'],'delegate_retrieval')


if __name__=='__main__':unittest.main()
