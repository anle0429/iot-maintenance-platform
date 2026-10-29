"""Check actual first-output semantics, private pattern guards and malformed/fragmented stream boundaries."""
from copy import deepcopy
import json,unittest
from llm_observability.contracts import MODEL,PromptPolicy,StreamObservation,agent_role,token_usage


def event(value):
    """Encode a genuine SSE frame, keeping non-ASCII bytes for fragmentation checks."""
    return ('data: '+json.dumps(value,ensure_ascii=False)+'\n\n').encode()


def question(text):
    """Build a text-only request without invoking a model or retaining a real person's information."""
    return {'model':MODEL,'stream':True,'messages':[{'role':'user','content':text}]}


class PromptPolicyTests(unittest.TestCase):
    """Keep PII/credential patterns and hardware-command boundaries explicit rather than claiming a universal classifier."""
    def test_all_categories_are_reported_once_without_retaining_input(self):
        """Detect declared synthetic examples without echoing their content into the result or object state."""
        policy=PromptPolicy();text='Please disable the pump safety interlock. Email learner@example.com; phone: +1 (202) 555-0199; api_key=synthetic_key_123456789.'
        result=policy.inspect(question(text+' '+text))
        self.assertEqual(result,['hardware_command','pii_email','pii_phone','secret_literal'])
        self.assertEqual(vars(policy),{})

    def test_safe_questions_and_reference_values_are_not_hardware_commands_or_phones(self):
        """Permit ordinary explanation, timestamps, machine IDs and numeric cohort questions."""
        for text in ['Explain why vibration does not prove failure.','Why should I not disable safety interlocks?',
                     'Read P-001 at 2026-11-29T23:50:00Z for 32 machines.','Start by explaining the pump context.']:
            with self.subTest(text=text):self.assertEqual(PromptPolicy().inspect(question(text)),[])

    def test_only_user_text_is_inspected_and_text_parts_are_supported(self):
        """Avoid treating the model's trusted policy or a returned tool result as a new human request."""
        payload=question('Read pump P-001.');payload['messages'].insert(0,{'role':'system','content':'Never disclose password=synthetic_token_value.'})
        payload['messages'].append({'role':'tool','content':'maintainer@example.com'})
        self.assertEqual(PromptPolicy().inspect(payload),[])
        payload['messages'][1]['content']=[{'type':'text','text':'email synthetic@example.com'}]
        self.assertEqual(PromptPolicy().inspect(payload),['pii_email'])

    def test_invalid_message_and_content_shapes_fail_before_inference(self):
        """Refuse unsupported user content, oversized text, unknown roles and ambiguous stream flags."""
        bad=[None,{},question('x')|{'model':'unreviewed-model'},question('x')|{'stream':1},question('x')|{'messages':[]},
             question('x')|{'messages':[None]},question('x')|{'messages':[{'role':'unknown','content':'x'}]},
             question('x')|{'messages':[{'role':'user','content':None}]},
             question('x')|{'messages':[{'role':'user','content':[{'type':'image_url','image_url':'private'}]}]},question('x'*65537)]
        for value in bad:
            with self.subTest(value=type(value).__name__),self.assertRaises(ValueError):PromptPolicy().inspect(value)

    def test_role_classification_uses_declared_tools_separately_from_invocation_counts(self):
        """Classify each supported tool set, including unknown names and ordinary direct inference."""
        cases=[([], 'direct'),(['assess_feature_drift'],'drift'),(['get_machine_features','predict_failure','search_knowledge'],'retrieval'),
               (['iot_agents__NS__iot_retrieval','iot_agents__NS__iot_drift'],'coordinator'),(['other'],'unknown')]
        for names,wanted in cases:
            with self.subTest(names=names):self.assertEqual(agent_role({'tools':[{'function':{'name':n}} for n in names]}),wanted)
        self.assertEqual(agent_role({'tools':None}),'unknown')

    def test_malformed_tool_names_remain_unknown_without_breaking_observation(self):
        """Unhashable or incomplete tool declarations must not crash a monitoring-only classifier."""
        for declarations in [[None],['tool'],[{}],[{'function':None}],[{'function':{'name':{}}}],[{'function':{'name':[]}}]]:
            with self.subTest(declarations=declarations):self.assertEqual(agent_role({'tools':declarations}),'unknown')


class StreamObservationTests(unittest.TestCase):
    """Known-answer protocol fixtures distinguish early headers/roles from actual output and terminal usage."""
    def test_role_only_delta_does_not_start_ttft_and_reported_usage_is_exact(self):
        """Start timing at the first text, not a heartbeat, role announcement or final usage frame."""
        stream=StreamObservation();stream.feed(b': heartbeat\n\n'+event({'choices':[{'delta':{'role':'assistant','tool_calls':None}}]}),0.1)
        self.assertIsNone(stream.first_output_seconds)
        stream.feed(event({'choices':[{'delta':{'content':'Pump'},'finish_reason':None}]}),0.75)
        stream.feed(event({'choices':[{'delta':{},'finish_reason':'stop'}]}),1.2)
        usage={'prompt_tokens':11,'completion_tokens':3,'total_tokens':14}
        stream.feed(event({'choices':[],'usage':usage})+b'data: [DONE]\n\n',1.3)
        result=stream.finish(1.4)
        self.assertTrue(result['complete']);self.assertEqual(result['ttft_seconds'],0.75);self.assertEqual(result['usage'],usage)
        self.assertEqual(result['duration_seconds'],1.4);self.assertNotIn('Pump',repr(vars(stream)))

    def test_all_byte_boundaries_and_multibyte_utf8_produce_the_same_counts(self):
        """A transport may split a JSON key or Vietnamese character at any byte without changing measurement facts."""
        raw=event({'choices':[{'delta':{'content':'Nhiệt độ'},'finish_reason':'stop'}]})+event({'usage':{'prompt_tokens':2,'completion_tokens':4,'total_tokens':6}})+b'data: [DONE]\n\n'
        for size in [1,2,3,7,31,len(raw)]:
            with self.subTest(chunk=size):
                stream=StreamObservation()
                for start in range(0,len(raw),size):stream.feed(raw[start:start+size],0.5)
                result=stream.finish(1);self.assertTrue(result['complete']);self.assertEqual(result['ttft_seconds'],0.5);self.assertEqual(result['usage']['total_tokens'],6)

    def test_tool_ids_and_role_metadata_are_not_generated_tool_content(self):
        """Measure the actual function name/arguments while ignoring the empty tool-call envelope."""
        stream=StreamObservation();stream.feed(event({'choices':[{'delta':{'tool_calls':[{'id':'native-id','type':'function','function':{}}]}}]}),0.1)
        self.assertIsNone(stream.first_output_seconds)
        stream.feed(event({'choices':[{'delta':{'tool_calls':[{'function':{'name':'get_machine_features','arguments':'{'}}]},'finish_reason':'tool_calls'}]}),0.4)
        stream.feed(event({'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}})+b'data: [DONE]\n\n',0.6)
        self.assertEqual(stream.finish(0.7)['ttft_seconds'],0.4)

    def test_truncated_absent_or_errored_output_is_not_a_complete_measurement(self):
        """Keep incomplete streams and upstream errors visible instead of inventing usage or completion."""
        stream=StreamObservation();stream.feed(event({'choices':[{'delta':{'content':'partial'}}]}),0.1)
        with self.assertRaises(ValueError):stream.finish(1)
        stream=StreamObservation();stream.feed(event({'error':{'message':'synthetic error'}})+b'data: [DONE]\n\n',0.1)
        self.assertFalse(stream.finish(1)['complete']);self.assertTrue(stream.upstream_error)
        stream=StreamObservation();stream.feed(event({'choices':[{'delta':{'content':'text'},'finish_reason':'stop'}]})+b'data: [DONE]\n\n',0.2)
        self.assertFalse(stream.finish(1)['complete']);self.assertIsNone(stream.usage)

    def test_invalid_frames_oversized_lines_and_conflicting_usage_are_rejected(self):
        """Exercise malformed JSON, invalid UTF-8, oversized data/comments and protocol-value partitions."""
        frames=[b'data: {bad}\n\n',b'data: []\n\n',b'\xff',b':'+b'x'*65536+b'\n',b'data: '+b'x'*65537+b'\n\n',
                event({'choices':{}}),event({'choices':[None]}),event({'choices':[{'delta':None}]}),
                event({'choices':[{'delta':{'content':1}}]}),event({'choices':[{'delta':{'tool_calls':{}}}]}),
                event({'choices':[{'delta':{'tool_calls':[{'function':{'arguments':{}}}]}}]}),
                event({'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':1}})]
        for raw in frames:
            with self.subTest(frame=raw[:25]),self.assertRaises(ValueError):StreamObservation().feed(raw,0.1)
        stream=StreamObservation();stream.feed(b'data: [DONE]\n\n',0.1)
        with self.assertRaises(ValueError):stream.feed(event({}),0.2)
        stream=StreamObservation();stream.feed(event({'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}),0.1)
        with self.assertRaises(ValueError):stream.feed(event({'usage':{'prompt_tokens':1,'completion_tokens':2,'total_tokens':3}}),0.2)

    def test_invalid_clock_values_do_not_enter_timings(self):
        """Reject booleans, negative/nonfinite times and durations preceding the first output."""
        for elapsed in [True,-1,float('nan'),float('inf'),3601]:
            with self.subTest(elapsed=elapsed),self.assertRaises(ValueError):StreamObservation().feed(b'',elapsed)
            with self.subTest(finish=elapsed),self.assertRaises(ValueError):
                stream=StreamObservation();stream.feed(b'data: [DONE]\n\n',0);stream.finish(elapsed)
        stream=StreamObservation();stream.feed(event({'choices':[{'delta':{'content':'x'},'finish_reason':'stop'}]})+b'data: [DONE]\n\n',1)
        with self.assertRaises(ValueError):stream.finish(0.5)

    def test_usage_counts_reject_boolean_negative_fraction_and_invalid_totals(self):
        """Use reported integers rather than coercing character counts or invalid provider metadata."""
        base={'prompt_tokens':2,'completion_tokens':3,'total_tokens':5}
        for key in base:
            for value in [True,-1,1.5,None,2**31]:
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):token_usage(base|{key:value})
        self.assertEqual(token_usage(base|{'provider_details':{'not_saved':True}}),base)


class ObservationBoundaryTests(unittest.TestCase):
    """Exercise independent protocol limits, valid extremes and first-output semantics rather than only visiting code lines."""
    def test_reported_usage_accepts_zero_and_int32_extremes_but_not_larger_totals(self):
        """Each count uses the advertised signed-int32 range; all sums must still be exact."""
        for a,b in [(0,0),(2147483647,0),(0,2147483647),(2147483646,1)]:
            expected={'prompt_tokens':a,'completion_tokens':b,'total_tokens':a+b}
            with self.subTest(a=a,b=b):self.assertEqual(token_usage(expected),expected)
        for value in [None,[],1,{'prompt_tokens':2147483649,'completion_tokens':0,'total_tokens':2147483649}]:
            with self.subTest(value=value),self.assertRaises(ValueError):token_usage(value)

    def test_conversation_and_text_limits_include_the_boundary(self):
        """Permit the documented last valid message/text size and reject the first unsupported size."""
        policy=PromptPolicy()
        self.assertEqual(policy.inspect(question('x'*65536)),[])
        self.assertEqual(policy.inspect(question('x')|{'messages':[{'role':'user','content':'x'}]*256}),[])
        for messages in [None,{},[{'role':'user','content':'x'}]*257]:
            with self.subTest(shape=type(messages).__name__),self.assertRaises(ValueError):policy.inspect(question('x')|{'messages':messages})
        for role in ['system','developer','assistant','tool']:
            with self.subTest(role=role):self.assertEqual(policy.inspect({'model':'iot-qwen2.5-7b','messages':[{'role':role,'content':'person@example.com'}]}),[])
        self.assertEqual(policy.inspect(question('x')|{'stream':False}),[])

    def test_fragment_clocks_and_wire_types_reject_invalid_partitions(self):
        """Reject booleans, nonfinite/negative clocks and nonbyte transport without coercion."""
        for elapsed in [-1,3600.0001,float('inf'),float('nan'),True,'1',None]:
            with self.subTest(clock=elapsed),self.assertRaises(ValueError):StreamObservation().feed(b'',elapsed)
        for chunk in ['',None,bytearray(b'')]:
            with self.subTest(chunk=type(chunk).__name__),self.assertRaises(ValueError):StreamObservation().feed(chunk,0)
        stream=StreamObservation();stream.feed(b'data: [DONE]\n\n',3600);self.assertEqual(stream.finish(3600)['duration_seconds'],3600)
        for elapsed in [-1,3600.0001,float('inf'),float('nan'),True,'1',None]:
            stream=StreamObservation();stream.feed(b'data: [DONE]\n\n',0)
            with self.subTest(finish=elapsed),self.assertRaises(ValueError):stream.finish(elapsed)

    def test_first_output_is_not_overwritten_and_refusal_is_observable(self):
        """Use different arrival times so replacing the first observation with the last cannot pass."""
        for delta in [{'content':'x'},{'refusal':'A synthetic refusal'},{'tool_calls':[{'function':{'arguments':'{}'}}]}]:
            with self.subTest(delta=delta):
                stream=StreamObservation();stream.feed(event({'choices':[{'delta':delta}]}),.2)
                stream.feed(event({'choices':[{'delta':{'content':'later'},'finish_reason':'length'}]}),.7)
                stream.feed(event({'usage':{'prompt_tokens':0,'completion_tokens':1,'total_tokens':1}})+b'data: [DONE]\n\n',.8)
                result=stream.finish(1)
                self.assertEqual(result,{'complete':True,'duration_seconds':1,'ttft_seconds':.2,
                    'usage':{'prompt_tokens':0,'completion_tokens':1,'total_tokens':1},'finish_reason':'length','upstream_error':False,'events':3})
                with self.assertRaises(ValueError):stream.finish(.1)

    def test_finish_reasons_and_repeated_identical_usage_preserve_provider_metadata(self):
        """Keep all supported reasons, normalize only unknown reasons and permit the same final count twice."""
        for reason,wanted in [('stop','stop'),('length','length'),('tool_calls','tool_calls'),('content_filter','content_filter'),('provider-specific','other')]:
            stream=StreamObservation();usage={'prompt_tokens':2,'completion_tokens':3,'total_tokens':5}
            stream.feed(event({'usage':usage})+event({'choices':[{'delta':{'content':'x'},'finish_reason':reason}]}),0)
            stream.feed(event({'usage':usage})+b'data: [DONE]\n\n',1)
            with self.subTest(reason=reason):self.assertEqual(stream.finish(1)['finish_reason'],wanted)

    def test_sse_limits_cover_pending_lines_and_multiline_event_accumulation(self):
        """A line budget and a combined data-event budget apply even before the event's blank separator arrives."""
        stream=StreamObservation();stream.feed(b':'+b'x'*65535+b'\r\n' + b'data:[DONE]\r\n\r\n',0);self.assertFalse(stream.finish(1)['complete'])
        for wire in [b'x'*65537,b'data: '+b'x'*40000+b'\ndata: '+b'x'*40000+b'\n',b'data: [DONE]\n\ndata: {}\n\n']:
            with self.subTest(bytes=len(wire)),self.assertRaises(ValueError):StreamObservation().feed(wire,0)
        stream=StreamObservation();stream.feed(b'\xe2',0)
        with self.assertRaises(ValueError):stream.finish(1)
        stream=StreamObservation();stream.feed(b'event: ping\nid: public\n: comment\ndata: [DONE]\n\n',0)
        self.assertEqual(stream.finish(1)['events'],0)

    def test_malformed_choice_fields_fail_without_fabricating_completion(self):
        """Partition every typed text/tool/reason field, including nested missing and malformed functions."""
        choices=[{'delta':{'refusal':1}},{'delta':{'tool_calls':[None]}},{'delta':{'tool_calls':[{'function':None}]}},
                 {'delta':{'tool_calls':[{'function':{'name':False}}]}},{'delta':{},'finish_reason':1}]
        for choice in choices:
            with self.subTest(choice=choice),self.assertRaises(ValueError):StreamObservation().feed(event({'choices':[choice]}),0)

    def test_nontext_roles_and_text_parts_fail_as_validation_errors(self):
        """Malformed JSON roles/content must never cause an incidental set/hash or regular-expression exception."""
        for role in [None,{},[],True,1]:
            value=question('x');value['messages'][0]['role']=role
            with self.subTest(role=role),self.assertRaises(ValueError):PromptPolicy().inspect(value)
        for text in [None,{},[],True,1]:
            value=question('x');value['messages'][0]['content']=[{'type':'text','text':text}]
            with self.subTest(text=text),self.assertRaises(ValueError):PromptPolicy().inspect(value)

    def test_terminal_marker_alone_does_not_supply_missing_finish_or_generated_output(self):
        """Keep individually missing terminal metadata distinct, including valid zero-duration observations."""
        for choices in [[{'delta':{'content':'x'}}],[{'delta':{'content':'','refusal':'','tool_calls':[{'function':{'name':'','arguments':''}}]},'finish_reason':'stop'}]]:
            stream=StreamObservation();stream.feed(event({'choices':choices})+event({'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}})+b'data: [DONE]\n\n',0)
            self.assertFalse(stream.finish(0)['complete'])
        stream=StreamObservation();stream.feed(event({'choices':[{'delta':{'content':'x'},'finish_reason':'stop'}]})+event({'usage':{'prompt_tokens':0,'completion_tokens':1,'total_tokens':1}})+b'data: [DONE]\n\n',0)
        self.assertTrue(stream.finish(0)['complete'])
        with self.assertRaises(ValueError):StreamObservation().feed(b'data: {"choices":[{"delta":{"content":"a\ndata: b"}}]}\n\n',0)


if __name__=='__main__':unittest.main()
