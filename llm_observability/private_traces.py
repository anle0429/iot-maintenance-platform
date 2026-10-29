"""Project native OTLP spans to approved identities, timings and counters before any trace reaches storage."""
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from llm_observability.contracts import MODEL,TOOLS
from llm_observability.ledger import validate_fact
import re

AGENTS={name:role for role in ['retrieval','drift','coordinator'] for name in [role,'iot-'+role,'iot_'+role,'iot-agents/iot-'+role]}
TOOL_NAMES={name:name for name in TOOLS}
TOOL_NAMES.update({name.replace('_','-'):name for name in TOOLS})
TOOL_NAMES.update({'iot_agents__NS__iot_retrieval':'delegate_retrieval','iot_agents__NS__iot_drift':'delegate_drift',
                   'iot-agents/iot-retrieval':'delegate_retrieval','iot-agents/iot-drift':'delegate_drift'})
AGENT_KEYS={'kagent.app_name','gen_ai.agent.name'}
TOOL_KEYS={'gen_ai.tool.name','gcp.vertex.agent.tool_name'}
MODEL_KEYS={'gen_ai.request.model','gen_ai.response.model'}
TOKEN_KEYS={'gen_ai.usage.input_tokens','gen_ai.usage.output_tokens','gen_ai.usage.prompt_tokens','gen_ai.usage.completion_tokens','gen_ai.usage.total_tokens'}
ID_KEYS={'gen_ai.task.id','gen_ai.conversation.id','iot.request_id'}
OPERATIONS={'invoke_agent','execute_tool','chat','generate_content','invocation'}


def attributes(items):
    """Read scalar attributes while refusing ambiguous repeated keys; nested content is never projected."""
    values={}
    for item in items:
        if item.key in values:raise ValueError('Ambiguous repeated OTLP attribute')
        field=item.value.WhichOneof('value');values[item.key]=getattr(item.value,field) if field else None
    return values


def approved(key,value,resource=False):
    """Keep a closed set of public telemetry values, excluding user IDs, URLs, messages, tool arguments and credentials."""
    if resource:
        if not isinstance(value,str):return False
        return (key=='service.name' and isinstance(value,str) and value in set(AGENTS)|{'iot_llm_observer'}
                or key=='service.namespace' and value in {'iot_agents','iot-agents','iot_llm','iot-llm'}
                or key=='service.version' and value in {'0.10.3','0.1.0'})
    if key in AGENT_KEYS:return isinstance(value,str) and value in AGENTS
    if key in TOOL_KEYS:return isinstance(value,str) and value in TOOL_NAMES
    if key in MODEL_KEYS:return value==MODEL
    if key in TOKEN_KEYS:return type(value) is int and 0<=value<=2**31-1
    if key=='gen_ai.operation.name':return isinstance(value,str) and value in OPERATIONS
    if key in ID_KEYS:return isinstance(value,str) and bool(re.fullmatch(r'(?:[a-f0-9]{32}|[a-f0-9]{48}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})',value))
    return False


def span_fact(span,resource):
    """Derive bounded count identities from real span attributes and exact start/end timestamps."""
    values=attributes(span.attributes);agents={AGENTS[v] for k,v in values.items() if k=='kagent.app_name' and isinstance(v,str) and v in AGENTS}
    if isinstance(resource.get('service.name'),str) and resource['service.name'] in AGENTS:agents.add(AGENTS[resource['service.name']])
    if len(agents)>1:raise ValueError('Conflicting native agent identities')
    agent=next(iter(agents),'unknown');operation=values.get('gen_ai.operation.name');name=span.name
    if not isinstance(operation,str):operation=None
    # In pinned Go ADK0.10.3, Executor.Execute creates exactly one root named invocation.
    # SDK invoke_agent spans are nested execution detail; remote target names need not equal the emitting service.
    if name=='invocation':kind='agent'
    elif operation=='invoke_agent' or name.startswith(('invoke_agent ','agent_run [')):kind='invocation'
    elif operation=='execute_tool' or name.startswith('execute_tool '):kind='tool'
    elif operation in {'chat','generate_content'} or name.startswith('generate_content'):kind='model'
    else:kind='other'
    tools={TOOL_NAMES[v] for k,v in values.items() if k in TOOL_KEYS and isinstance(v,str) and v in TOOL_NAMES}
    if len(tools)>1:raise ValueError('Conflicting native tool identities')
    tool=next(iter(tools),'none');models={v for k,v in values.items() if k in MODEL_KEYS and v==MODEL}
    usage=None
    counts=[]
    for group in [('gen_ai.usage.input_tokens','gen_ai.usage.prompt_tokens'),('gen_ai.usage.output_tokens','gen_ai.usage.completion_tokens')]:
        found={values[key] for key in group if key in values and type(values[key]) is int and 0<=values[key]<=2**31-1}
        if len(found)>1:raise ValueError('Conflicting native token aliases')
        counts.append(next(iter(found),None))
    if all(v is not None for v in counts):
        usage={'prompt_tokens':counts[0],'completion_tokens':counts[1],'total_tokens':sum(counts)}
        if 'gen_ai.usage.total_tokens' in values and values['gen_ai.usage.total_tokens']!=usage['total_tokens']:raise ValueError('Inconsistent native trace usage')
    if span.status.code not in {0,1,2}:raise ValueError('Invalid native span status')
    if len(span.trace_id)!=16 or len(span.span_id)!=8 or len(span.parent_span_id) not in {0,8}:raise ValueError('Invalid OTLP identity sizes')
    fact={'agent':agent,'model':MODEL if models else 'unknown','duration_seconds':(span.end_time_unix_nano-span.start_time_unix_nano)/1e9,
          'usage':usage,'operation':kind,'tool':tool,'error':span.status.code==2,'status_code':span.status.code,'trace_id':span.trace_id.hex(),'span_id':span.span_id.hex(),
          'parent_span_id':span.parent_span_id.hex(),'start_ns':span.start_time_unix_nano,'end_ns':span.end_time_unix_nano}
    validate_fact('span',fact);return fact


class PrivateTraceProcessor:
    """Preserve actual graph IDs/times/status while removing all unapproved text, events, links and resource data."""
    def project(self,raw):
        """Validate a bounded native OTLP batch before exporting its privacy projection or counting any span."""
        if not isinstance(raw,bytes) or not 0<len(raw)<=2*1024**2:raise ValueError('Invalid OTLP request size')
        request=ExportTraceServiceRequest()
        try:request.ParseFromString(raw)
        except DecodeError:raise ValueError('Invalid OTLP trace message') from None
        request.DiscardUnknownFields();facts=[];removed=0
        for resource_spans in request.resource_spans:
            values=attributes(resource_spans.resource.attributes)
            kept=[item for item in resource_spans.resource.attributes if approved(item.key,values[item.key],resource=True)]
            removed+=len(resource_spans.resource.attributes)-len(kept)
            resource_spans.resource.Clear();resource_spans.resource.attributes.extend(kept);resource_spans.schema_url=''
            for scope_spans in resource_spans.scope_spans:
                scope_spans.scope.Clear();scope_spans.schema_url=''
                for span in scope_spans.spans:
                    fact=span_fact(span,values);facts.append((fact['trace_id']+fact['span_id'],fact))
                    if len(facts)>1024:raise ValueError('OTLP batch exceeds the observation budget')
                    span_values=attributes(span.attributes);kept=[item for item in span.attributes if approved(item.key,span_values[item.key])]
                    removed+=len(span.attributes)-len(kept)
                    identity={key:getattr(span,key) for key in ['trace_id','span_id','parent_span_id','start_time_unix_nano','end_time_unix_nano','kind','flags']}
                    status=span.status.code;span.Clear()
                    for key,value in identity.items():setattr(span,key,value)
                    span.attributes.extend(kept);span.status.code=status
                    span.name=fact['operation']+' '+(fact['tool'] if fact['operation']=='tool' else fact['model'] if fact['operation']=='model' else fact['agent'])
        if not facts:raise ValueError('Empty OTLP trace batch')
        return {'payload':request.SerializeToString(deterministic=True),'spans':facts,'received_spans':len(facts),'removed_attributes':removed}
