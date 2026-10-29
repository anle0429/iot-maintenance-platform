"""Render bounded Prometheus series from durable approved facts, preserving token/TTFT and trace scopes."""
from collections import defaultdict
from llm_observability.ledger import validate_fact

SECONDS=(.01,.025,.05,.1,.25,.5,1,2.5,5,10,30,60,120,300,600,3600)
TOKENS=(0,1,8,32,128,512,2048,8192,32768,131072,2147483647)


def render_metrics(rows):
    """Rebuild counters/histograms from unique persisted events so restart or OTLP replay cannot inflate counts."""
    values=defaultdict(float);types={};seen=set()
    def add(name,labels,value=1,kind='counter'):
        """Aggregate only code-defined metric/label names and validated scalar labels."""
        key=(name,tuple(sorted((k,str(v)) for k,v in labels.items())))
        values[key]+=value;types[name]=kind
    def histogram(name,labels,value,buckets):
        """Emit cumulative finite buckets plus the required infinity bucket, sum and count."""
        for bound in buckets:add(name+'_bucket',labels|{'le':format(bound,'.17g')},int(value<=bound),'histogram')
        add(name+'_bucket',labels|{'le':'+Inf'},1,'histogram');add(name+'_sum',labels,value,'histogram');add(name+'_count',labels,1,'histogram')
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'kind','event_id','facts'}:raise ValueError('Unreviewed ledger row')
        key=(row['kind'],row['event_id'])
        if key in seen:raise ValueError('Duplicate telemetry identity in metric input')
        seen.add(key);kind=row['kind'];fact=validate_fact(kind,row['facts']);labels={'agent':fact['agent'],'model':fact['model']}
        add('iot_llm_telemetry_stored_events',{'kind':kind},1,'gauge')
        if kind=='request':
            add('iot_llm_requests_total',labels|{'result':fact['result'],'status':fact['status'],'streaming':str(fact['streaming']).lower()})
            if fact['finish_reason'] is not None:add('iot_llm_completion_outcomes_total',labels|{'finish_reason':fact['finish_reason']})
            histogram('iot_llm_request_seconds',labels,fact['duration_seconds'],SECONDS)
            if fact['ttft_seconds'] is not None:histogram('iot_llm_first_output_seconds',labels,fact['ttft_seconds'],SECONDS)
            usage=fact['usage']
            if usage is not None:
                for name,field in [('input','prompt_tokens'),('output','completion_tokens'),('total','total_tokens')]:
                    add('iot_llm_reported_tokens_total',labels|{'type':name},usage[field])
                    histogram('iot_llm_reported_token_count',labels|{'type':name},usage[field],TOKENS)
            elif 200<=fact['status']<300:add('iot_llm_usage_missing_total',labels)
            if fact['result']!='invalid_request':
                add('iot_llm_policy_checks_total',{'outcome':'blocked' if fact['policy_categories'] else 'allowed'})
                for category in fact['policy_categories']:add('iot_llm_policy_hits_total',{'category':category})
        else:
            trace_labels=labels|{'operation':fact['operation'],'tool':fact['tool'],'result':{0:'unset',1:'success',2:'failure'}[fact['status_code']]}
            add('iot_llm_observed_spans_total',trace_labels)
            histogram('iot_llm_observed_span_seconds',trace_labels,fact['duration_seconds'],SECONDS)
            if fact['operation']=='agent' and fact['agent'] in {'retrieval','drift','coordinator'}:
                add('iot_agent_invocations_total',{'agent':fact['agent'],'result':trace_labels['result']})
            if fact['operation']=='tool' and fact['tool']!='none':
                add('iot_agent_traced_tool_calls_total',{'agent':fact['agent'],'tool':fact['tool'],'result':trace_labels['result']})
    lines=[]
    for name in sorted(types):
        metric_type=types[name]
        if metric_type=='histogram':continue
        lines.append('# TYPE '+name+' '+metric_type)
    histogram_names={name.removesuffix('_bucket') for name in types if name.endswith('_bucket')}
    for name in sorted(histogram_names):lines.append('# TYPE '+name+' histogram')
    for (name,labels),value in sorted(values.items()):
        # Labels are bounded by validate_fact; this final encoding also protects the exposition grammar.
        encoded=','.join(k+'="'+v.replace('\\','\\\\').replace('\n','\\n').replace('"','\\"')+'"' for k,v in labels)
        lines.append(name+'{'+encoded+'} '+format(value,'.17g'))
    return ('\n'.join(lines)+'\n').encode()
