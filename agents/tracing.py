"""Observe real A2A client calls, propagate W3C context and export bounded terminal-state spans automatically."""
import json
import re
import secrets
import time
from urllib.request import ProxyHandler,Request,build_opener

ROLES={'retrieval','drift','coordinator'}
PENDING={'submitted','working','input-required','auth-required'}
FAILED={'failed','canceled','rejected'}


def attribute(key,value):
    """Encode one code-defined scalar; callers never pass prompt, response, URL or credential strings."""
    return {'key':key,'value':{'intValue':str(value)} if type(value) is int else {'stringValue':value}}


def otlp_sender(endpoint):
    """Export only this application's bounded metadata to an explicitly selected private HTTP OTLP receiver."""
    from urllib.parse import urlsplit
    parsed=urlsplit(endpoint)
    if parsed.scheme!='http' or parsed.path!='/v1/traces' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Use the private HTTP OTLP receiver without embedded credentials or query parameters')
    opener=build_opener(ProxyHandler({}))
    def send(payload):
        """Treat non-200 or partial rejection as an export failure, independently of the agent's actual result."""
        with opener.open(Request(endpoint,json.dumps(payload,separators=(',',':')).encode(),{'Content-Type':'application/json'}),timeout=3) as response:
            body=json.load(response)
            if response.status!=200 or body.get('partialSuccess',{}).get('rejectedSpans',0):
                raise ValueError('Private OTLP receiver rejected the client span')
    return send


class A2AObservation:
    """Own one actual outbound call span; SDK spans and their original statuses remain untouched."""
    def __init__(self,role,*,send,clock=time.time_ns):
        """Create the caller context before dispatch so downstream spans can reference this real parent."""
        if role not in ROLES:raise ValueError('Unreviewed A2A role')
        self.role=role;self.send=send;self.clock=clock;self.start=clock();self.finished=False
        self.trace_id=secrets.token_hex(16);self.span_id=secrets.token_hex(8)
        self.headers={'traceparent':'00-'+self.trace_id+'-'+self.span_id+'-01'}

    def finish(self,status,body,*,transport_error=None):
        """Set OK only for an observed completed task; distinguish pending, task, RPC, HTTP and transport outcomes."""
        if self.finished:raise ValueError('A2A observation already finished')
        self.finished=True;end=self.clock();state='invalid_response';task_id=None;code=2
        if transport_error:state='transport_error'
        elif status is None or not 200<=status<300:state='http_error'
        else:
            try:
                reply=json.loads(body)
                if not isinstance(reply,dict):raise ValueError('Invalid response')
                if reply.get('error'):state='rpc_error'
                else:
                    task=reply['result'];observed=task['status']['state']
                    if observed=='completed':state=observed;code=1
                    elif observed in PENDING:state=observed;code=0
                    elif observed in FAILED:state=observed
                    identity=task.get('id')
                    if isinstance(identity,str) and re.fullmatch(r'(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})',identity):task_id=identity
            except (ValueError,KeyError,TypeError):pass
        attrs=[attribute('gen_ai.agent.name','iot_'+self.role),attribute('a2a.task.state',state)]
        if task_id:attrs.append(attribute('gen_ai.task.id',task_id))
        if status is not None:attrs.append(attribute('http.response.status_code',status))
        span={'traceId':self.trace_id,'spanId':self.span_id,'name':'A2A '+self.role+' '+state,'kind':3,
              'startTimeUnixNano':str(self.start),'endTimeUnixNano':str(end),'status':{'code':code},'attributes':attrs}
        resource={'resource':{'attributes':[attribute('service.name','iot-a2a-client'),attribute('service.namespace','iot-agents')]},
                  'scopeSpans':[{'scope':{'name':'iot.a2a.client','version':'1.0.0'},'spans':[span]}]}
        report={'trace_id':self.trace_id,'span_id':self.span_id,'task_id':task_id,'task_state':state,'status_code':code,'exported':False}
        try:self.send({'resourceSpans':[resource]});report['exported']=True
        except (OSError,ValueError) as error:report['export_error']=type(error).__name__
        return report
