"""Validate bounded text prompts and extract actual SSE timing/usage without keeping generated content."""
from codecs import getincrementaldecoder
from math import isfinite
import json
import re

MODEL='iot-qwen2.5-7b'
ROLES={'retrieval','drift','coordinator','direct','unknown'}
TOOLS={'get_machine_features','predict_failure','search_knowledge','assess_feature_drift'}
CATEGORIES={'pii_email','pii_phone','secret_literal','hardware_command'}


def token_usage(value):
    """Require reported nonnegative integer counts and an exact total; never estimate tokens from characters."""
    keys=('prompt_tokens','completion_tokens','total_tokens')
    if not isinstance(value,dict) or any(type(value.get(k)) is not int or not 0<=value[k]<=2**31-1 for k in keys):
        raise ValueError('Invalid reported token usage')
    result={k:value[k] for k in keys}
    if result['prompt_tokens']+result['completion_tokens']!=result['total_tokens']:raise ValueError('Inconsistent reported token usage')
    return result


def agent_role(payload):
    """Classify model turns by their declared tool set, separately from actual traced agent invocation counts."""
    declarations=payload.get('tools',[])
    if not isinstance(declarations,list):return 'unknown'
    names=set()
    for declaration in declarations:
        if not isinstance(declaration,dict) or not isinstance(declaration.get('function'),dict):return 'unknown'
        name=declaration['function'].get('name')
        if not isinstance(name,str) or not name:return 'unknown'
        names.add(name)
    if names=={'get_machine_features','predict_failure','search_knowledge'}:return 'retrieval'
    if names=={'assess_feature_drift'}:return 'drift'
    if names=={'iot_agents__NS__iot_retrieval','iot_agents__NS__iot_drift'}:return 'coordinator'
    return 'direct' if not names else 'unknown'


class PromptPolicy:
    """Block explicitly defined PII/credential patterns and direct hardware commands before inference, without retaining text."""
    patterns={
        'pii_email':re.compile(r'(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}\b'),
        'pii_phone':re.compile(r'\b(?:phone|mobile|tel)\s*[:=]\s*\+?[0-9][0-9 ()-]{7,24}[0-9]\b',re.I),
        'secret_literal':re.compile(r'\b(?:api[_ -]?key|password|access[_ -]?token|bearer[_ -]?token)\s*[:=]\s*[A-Za-z0-9_./+=-]{12,256}',re.I),
        'hardware_command':re.compile(r'^\s*(?:please\s+)?(?:(?:disable|bypass)\b[^\n]{0,60}\b(?:interlocks?|safety|alarms?)\b|(?:start|stop|turn\s+off)\s+(?:the\s+)?(?:pump|machine)\b|(?:set|change|increase|decrease)\s+(?:the\s+)?(?:pump\s+)?rpm\b)',re.I|re.M)}

    def inspect(self,payload):
        """Inspect only user text; structured tool output and trusted system instructions are different inputs."""
        if not isinstance(payload,dict) or payload.get('model')!=MODEL or type(payload.get('stream',False)) is not bool:
            raise ValueError('Require the pinned text model and a boolean streaming mode')
        messages=payload.get('messages')
        if not isinstance(messages,list) or not 1<=len(messages)<=256:raise ValueError('Require bounded conversation messages')
        found=set()
        for message in messages:
            if not isinstance(message,dict) or message.get('role') not in {'system','developer','user','assistant','tool'}:
                raise ValueError('Invalid message role')
            if message['role']!='user':continue
            content=message.get('content')
            if isinstance(content,str):texts=[content]
            elif isinstance(content,list) and all(isinstance(p,dict) and p.get('type')=='text' and isinstance(p.get('text'),str) for p in content):
                texts=[p['text'] for p in content]
            else:raise ValueError('Require text-only user messages')
            for text in texts:
                if len(text)>65536:raise ValueError('User text exceeds the observed text-only contract')
                found.update(name for name,pattern in self.patterns.items() if pattern.search(text))
        return sorted(found)


class StreamObservation:
    """Incrementally measure the first generated text/tool fragment and final usage from unmodified SSE bytes."""
    def __init__(self):
        """Keep bounded decoder/event state, timing and counts; no generated text is accumulated."""
        self.decoder=getincrementaldecoder('utf-8')('strict');self.pending='';self.data=[];self.event_chars=0
        self.first_output_seconds=None;self.usage=None;self.done=False;self.finish_reason=None;self.events=0;self.upstream_error=False

    def feed(self,chunk,elapsed_seconds):
        """Accept arbitrarily split UTF-8/SSE chunks and ignore headers, comments and role-only deltas for TTFT."""
        if not isinstance(chunk,bytes) or type(elapsed_seconds) not in {int,float} or not isfinite(elapsed_seconds) or not 0<=elapsed_seconds<=3600:raise ValueError('Invalid stream observation')
        try:self.pending+=self.decoder.decode(chunk)
        except UnicodeError:raise ValueError('Invalid UTF-8 event stream') from None
        while '\n' in self.pending:
            line,self.pending=self.pending.split('\n',1);self._line(line.removesuffix('\r'),elapsed_seconds)
        if len(self.pending)>65536:raise ValueError('SSE line exceeds the observation budget')

    def _line(self,line,elapsed):
        """Assemble one bounded SSE data event while ignoring standard comments and non-data fields."""
        if len(line)>65536:raise ValueError('SSE line exceeds the observation budget')
        if not line:
            if self.data:self._event('\n'.join(self.data),elapsed)
            self.data=[];self.event_chars=0;return
        if line.startswith('data:'):
            text=line[5:].removeprefix(' ');self.event_chars+=len(text)
            if self.event_chars>65536:raise ValueError('SSE event exceeds the observation budget')
            self.data.append(text)

    def _event(self,text,elapsed):
        """Count a real output fragment rather than the response header or a fabricated token estimate."""
        if self.done:raise ValueError('Unexpected data after the final SSE marker')
        if text=='[DONE]':self.done=True;return
        try:value=json.loads(text)
        except (ValueError,RecursionError):raise ValueError('Invalid SSE JSON') from None
        if not isinstance(value,dict):raise ValueError('Require an SSE JSON object')
        self.events+=1
        if value.get('error'):self.upstream_error=True;return
        usage=value.get('usage')
        if usage is not None:
            usage=token_usage(usage)
            if self.usage is not None and self.usage!=usage:raise ValueError('Conflicting final usage events')
            self.usage=usage
        choices=value.get('choices',[])
        if not isinstance(choices,list):raise ValueError('Invalid streamed choices')
        for choice in choices:
            if not isinstance(choice,dict) or not isinstance(choice.get('delta',{}),dict):raise ValueError('Invalid streamed delta')
            delta=choice.get('delta',{});content=delta.get('content');refusal=delta.get('refusal');calls=delta.get('tool_calls',[])
            if calls is None:calls=[]
            if content is not None and not isinstance(content,str) or refusal is not None and not isinstance(refusal,str):raise ValueError('Invalid streamed text')
            if not isinstance(calls,list):raise ValueError('Invalid streamed tool fragments')
            output=bool(content or refusal)
            for call in calls:
                if not isinstance(call,dict) or not isinstance(call.get('function',{}),dict):raise ValueError('Invalid streamed tool function')
                function=call.get('function',{})
                for field in ['name','arguments']:
                    if field in function and not isinstance(function[field],str):raise ValueError('Invalid tool fragment value')
                output=output or bool(function.get('name') or function.get('arguments'))
            if output and self.first_output_seconds is None:self.first_output_seconds=elapsed
            reason=choice.get('finish_reason')
            if reason is not None:
                if not isinstance(reason,str):raise ValueError('Invalid finish reason')
                self.finish_reason=reason if reason in {'stop','length','tool_calls','content_filter'} else 'other'

    def finish(self,elapsed_seconds):
        """Reject truncation or absent usage/output rather than publishing apparently complete measurements."""
        try:tail=self.decoder.decode(b'',final=True)
        except UnicodeError:raise ValueError('Truncated UTF-8 event stream') from None
        self.pending+=tail
        if self.pending or self.data or not self.done:raise ValueError('Incomplete SSE response')
        if type(elapsed_seconds) not in {int,float} or not isfinite(elapsed_seconds) or not 0<=elapsed_seconds<=3600 or self.first_output_seconds is not None and elapsed_seconds<self.first_output_seconds:
            raise ValueError('Invalid stream duration')
        complete=not self.upstream_error and self.usage is not None and self.first_output_seconds is not None and self.finish_reason is not None
        return {'complete':complete,'duration_seconds':elapsed_seconds,'ttft_seconds':self.first_output_seconds,'usage':self.usage,
                'finish_reason':self.finish_reason,'upstream_error':self.upstream_error,'events':self.events}
