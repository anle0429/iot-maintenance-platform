"""Measure bounded real GPU generation through the retained authenticated observer with a declared lab SLA."""
from pathlib import Path
import json,os
from locust import HttpUser,task,constant_pacing,events

SLA={'minimum_requests':20,'maximum_failures':0,'p95_ms':5000}
RECORDS=[]


class ModelReader(HttpUser):
    """Pace a fixed synthetic prompt and validate provider usage instead of counting HTTP 200 alone."""
    wait_time=constant_pacing(1)
    def on_start(self):
        """Read the private bearer file only in memory; Locust commands/reports contain no token."""
        self.client.headers['Authorization']='Bearer '+Path(os.environ['IOT_LLM_LOAD_TOKEN_FILE']).read_text().strip()
    @task
    def generate(self):
        """Consume the whole SSE reply and require completed framing, actual output and consistent provider usage."""
        payload={'model':'iot-qwen2.5-7b','stream':True,'stream_options':{'include_usage':True},'temperature':0,'seed':7,'max_tokens':16,
            'messages':[{'role':'system','content':'Answer in at most one sentence about the synthetic IoT coursework.'},
                        {'role':'user','content':'Explain why duplicated sensor events distort a rolling average.'}]}
        context={}
        with self.client.post('/v1/chat/completions',json=payload,name='streamed generation / 16-token limit',catch_response=True,context=context) as response:
            if response.status_code!=200:return
            try:
                lines=[line[5:].strip() for line in response.text.splitlines() if line.startswith('data:')]
                assert lines and lines[-1]=='[DONE]' and len(response.content)<=1048576
                chunks=[json.loads(v) for v in lines[:-1]];usage=next(v['usage'] for v in reversed(chunks) if v.get('usage'))
                assert all(type(usage[n]) is int and usage[n]>0 for n in ['prompt_tokens','completion_tokens','total_tokens'])
                assert usage['prompt_tokens']+usage['completion_tokens']==usage['total_tokens'] and usage['completion_tokens']<=16
                assert any(choice.get('delta',{}).get('content') for chunk in chunks for choice in chunk.get('choices',[]))
                assert any(choice.get('finish_reason') in {'stop','length'} for chunk in chunks for choice in chunk.get('choices',[]))
                assert len(response.headers['x-iot-request-id'])==32
                context.update({'request_id':response.headers['x-iot-request-id'],'usage':{n:usage[n] for n in ['prompt_tokens','completion_tokens','total_tokens']}})
            except (ValueError,KeyError,AssertionError,StopIteration):response.failure('Incomplete stream, missing output or inconsistent provider usage')


@events.request.add_listener
def record(request_type,name,response_time,response_length,response=None,context=None,exception=None,**kwargs):
    """Keep per-request completion timing and approved usage; omit prompt, completion and authentication text."""
    RECORDS.append({'request_id':(context or {}).get('request_id'),'usage':(context or {}).get('usage'),'response_ms':response_time,
        'response_bytes':response_length,'status':response.status_code if response is not None else None,'failed':exception is not None})


@events.quitting.add_listener
def enforce_sla(environment,**kwargs):
    """Preserve the full paced-workload report and fail when its predeclared lab gate is missed."""
    total=environment.stats.total;p95=total.get_response_time_percentile(.95)
    passed=total.num_requests>=SLA['minimum_requests'] and total.num_failures==0 and p95 is not None and p95<=SLA['p95_ms']
    report={'passed':passed,'sla':SLA,'requests':total.num_requests,'failures':total.num_failures,'requests_per_second':total.total_rps,
        'p50_ms':total.get_response_time_percentile(.5),'p95_ms':p95,'p99_ms':total.get_response_time_percentile(.99),'records':RECORDS,
        'scope':'Paced clients on the CPU VM, authenticated observer and actual GPU streaming generation capped at 16 output tokens. No maximum capacity, external-network SLA, agent/MCP or generic prompt-quality claim.'}
    Path(os.environ['IOT_LLM_LOAD_SUMMARY']).write_text(json.dumps(report,indent=2)+'\n')
    if not passed:environment.process_exit_code=1
