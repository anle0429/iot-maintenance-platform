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


class TraceSchemaTests(unittest.TestCase):
    """Exercise the independent public metadata schema and original graph values across every supported role/tool alias."""
    def test_public_resource_schema_is_closed_and_typed(self):
        """Permit only reviewed service identities, namespaces and versions; reject other values or resource keys."""
        roles=['retrieval','drift','coordinator'];names=[v for role in roles for v in [role,'iot-'+role,'iot_'+role,'iot-agents/iot-'+role]]+['iot_llm_observer']
        fields={'service.name':names,'service.namespace':['iot_agents','iot-agents','iot_llm','iot-llm'],'service.version':['0.10.3','0.1.0']}
        for key,values in fields.items():
            for value in values:
                with self.subTest(key=key,value=value):self.assertTrue(approved(key,value,True))
            for value in ['private@example.com',None,True,123,[]]:
                with self.subTest(key=key,rejected=value):self.assertFalse(approved(key,value,True))
        self.assertFalse(approved('gen_ai.agent.name','iot_retrieval',True))
        self.assertFalse(approved('service.name','iot_retrieval',False))

    def test_public_span_schema_preserves_only_reviewed_scalar_values(self):
        """Keep supported agent/tool/model/operation/count/opaque-ID metadata without accepting free text or boolean counts."""
        values={'kagent.app_name':['retrieval','iot-drift','iot_coordinator','iot-agents/iot-retrieval'],
            'gen_ai.agent.name':['retrieval','iot-drift','iot_coordinator','iot-agents/iot-retrieval'],
            'gen_ai.tool.name':['get_machine_features','predict-failure','search_knowledge','assess-feature-drift','iot_agents__NS__iot_retrieval','iot-agents/iot-drift'],
            'gcp.vertex.agent.tool_name':['get-machine-features','predict_failure','search-knowledge','assess_feature_drift','iot-agents/iot-retrieval','iot_agents__NS__iot_drift'],
            'gen_ai.request.model':['iot-qwen2.5-7b'],'gen_ai.response.model':['iot-qwen2.5-7b'],
            'gen_ai.operation.name':['invoke_agent','execute_tool','chat','generate_content','invocation']}
        for key,items in values.items():
            for value in items:
                with self.subTest(key=key,value=value):self.assertTrue(approved(key,value))
            self.assertFalse(approved(key,'private unknown text'))
        for key in ['gen_ai.usage.input_tokens','gen_ai.usage.output_tokens','gen_ai.usage.prompt_tokens','gen_ai.usage.completion_tokens','gen_ai.usage.total_tokens']:
            for value in [0,2147483647]:self.assertTrue(approved(key,value))
            for value in [-1,2147483648,2147483649,True,1.0,'1',None]:self.assertFalse(approved(key,value))
        for key in ['gen_ai.task.id','gen_ai.conversation.id','iot.request_id']:
            for value in ['a'*32,'b'*48,'12345678-1234-1234-abcd-123456789abc']:self.assertTrue(approved(key,value))
            for value in ['a'*31,'b'*49,'private@example.com',None,32]:self.assertFalse(approved(key,value))

    def test_original_span_graph_status_and_canonical_name_are_preserved(self):
        """Check flags/kind, root parentage, status zero/one/two and the complete derived identity rather than only error counts."""
        for status in [0,1,2]:
            request,span=batch();span.status.code=status;span.kind=3;span.flags=257;span.parent_span_id=b''
            result=PrivateTraceProcessor().project(request.SerializeToString());projected=ExportTraceServiceRequest.FromString(result['payload'])
            actual=projected.resource_spans[0].scope_spans[0].spans[0];fact=result['spans'][0][1]
            self.assertEqual((actual.name,actual.kind,actual.flags,actual.parent_span_id,actual.status.code),('tool predict_failure',3,257,b'',status))
            self.assertEqual(fact,{'agent':'retrieval','model':'unknown','duration_seconds':.5,'usage':None,'operation':'tool','tool':'predict_failure',
                'error':status==2,'status_code':status,'trace_id':(b'a'*16).hex(),'span_id':(b'b'*8).hex(),'parent_span_id':'','start_ns':1000000000,'end_ns':1500000000})
            self.assertEqual(result['spans'][0][0],(b'a'*16+b'b'*8).hex());self.assertEqual(result['received_spans'],1);self.assertEqual(result['removed_attributes'],3)

    def test_each_known_alias_maps_to_the_emitter_and_canonical_tool(self):
        """Use every supported service spelling and delegation alias so unknown classification cannot hide a dropped identity."""
        for role in ['retrieval','drift','coordinator']:
            for alias in [role,'iot-'+role,'iot_'+role,'iot-agents/iot-'+role]:
                request,span=batch();next(a for a in request.resource_spans[0].resource.attributes if a.key=='service.name').value.string_value=alias
                span.attributes.add(key='kagent.app_name').value.string_value=alias
                self.assertEqual(PrivateTraceProcessor().project(request.SerializeToString())['spans'][0][1]['agent'],role)
        tools=['get_machine_features','predict_failure','search_knowledge','assess_feature_drift']
        pairs=[(alias,name) for name in tools for alias in [name,name.replace('_','-')]]
        pairs += [('iot_agents__NS__iot_retrieval','delegate_retrieval'),('iot-agents/iot-retrieval','delegate_retrieval'),
                  ('iot_agents__NS__iot_drift','delegate_drift'),('iot-agents/iot-drift','delegate_drift')]
        for alias,wanted in pairs:
            request,span=batch();next(a for a in span.attributes if a.key=='gen_ai.tool.name').value.string_value=alias
            self.assertEqual(PrivateTraceProcessor().project(request.SerializeToString())['spans'][0][1]['tool'],wanted)

    def test_reported_aliases_model_and_operation_are_not_invented(self):
        """Preserve matching aliases and reported models, reject conflicting totals and leave incomplete usage absent."""
        for input_key,output_key in [('gen_ai.usage.input_tokens','gen_ai.usage.output_tokens'),('gen_ai.usage.prompt_tokens','gen_ai.usage.completion_tokens')]:
            request,span=batch()
            for key,value in [(input_key,0),(output_key,2147483647),('gen_ai.usage.total_tokens',2147483647)]:span.attributes.add(key=key).value.int_value=value
            for key in ['gen_ai.request.model','gen_ai.response.model']:span.attributes.add(key=key).value.string_value='iot-qwen2.5-7b'
            result=PrivateTraceProcessor().project(request.SerializeToString())['spans'][0][1]
            self.assertEqual(result['model'],'iot-qwen2.5-7b');self.assertEqual(result['usage'],{'prompt_tokens':0,'completion_tokens':2147483647,'total_tokens':2147483647})
            next(a for a in span.attributes if a.key=='gen_ai.usage.total_tokens').value.int_value=1
            with self.assertRaises(ValueError):PrivateTraceProcessor().project(request.SerializeToString())
        request,span=batch();span.attributes.add(key='gen_ai.usage.input_tokens').value.int_value=7
        self.assertIsNone(PrivateTraceProcessor().project(request.SerializeToString())['spans'][0][1]['usage'])
        request,span=batch();span.attributes.add(key='gcp.vertex.agent.tool_name').value.string_value='search_knowledge'
        with self.assertRaises(ValueError):PrivateTraceProcessor().project(request.SerializeToString())

    def test_unknown_origin_and_unapproved_typed_metadata_remain_bounded(self):
        """Unsupported native metadata is removed without crashing or fabricating a known agent/model/tool identity."""
        request,span=batch();resource=request.resource_spans[0].resource
        next(a for a in resource.attributes if a.key=='service.name').value.string_value='private service name'
        span.name='HTTP request';del span.attributes[:]
        span.attributes.add(key='gen_ai.operation.name').value.array_value.values.add().string_value='SECRET_VALUE'
        result=PrivateTraceProcessor().project(request.SerializeToString());fact=result['spans'][0][1]
        self.assertEqual((fact['agent'],fact['model'],fact['tool'],fact['operation'],fact['usage']),('unknown','unknown','none','other',None))
        self.assertNotIn(b'SECRET_VALUE',result['payload']);self.assertNotIn(b'private service',result['payload'])

    def test_batch_and_duration_limits_accept_the_boundary_and_reject_the_next_value(self):
        """Check the 1024-span admission limit and exact one-hour duration independently of payload byte limits."""
        request,span=batch();span.end_time_unix_nano=span.start_time_unix_nano+3600*1000000000
        self.assertEqual(PrivateTraceProcessor().project(request.SerializeToString())['spans'][0][1]['duration_seconds'],3600)
        span.end_time_unix_nano+=1
        with self.assertRaises(ValueError):PrivateTraceProcessor().project(request.SerializeToString())
        request,span=batch();scope=request.resource_spans[0].scope_spans[0]
        for _ in range(1023):scope.spans.add().CopyFrom(span)
        self.assertEqual(PrivateTraceProcessor().project(request.SerializeToString())['received_spans'],1024)
        scope.spans.add().CopyFrom(span)
        with self.assertRaises(ValueError):PrivateTraceProcessor().project(request.SerializeToString())
        request,span=batch();span.status.code=3
        with self.assertRaises(ValueError):PrivateTraceProcessor().project(request.SerializeToString())


if __name__=='__main__':unittest.main()
