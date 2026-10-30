"""Define paired model-artifact questions and independent strict scoring for a small synthetic IoT reasoning experiment."""
from collections import Counter
from math import isfinite
from statistics import mean,median
import json

MODELS={'small':'iot-qwen2.5-1.5b','large':'iot-qwen2.5-7b'}
CASES=[
    {'id':'inclusive_upper_boundary','operating':True,'future_minutes':90,'failure_after_minutes':60,
     'expected':{'label':1,'machine_operating':True,'reason':'failure_in_horizon'}},
    {'id':'failure_after_horizon','operating':True,'future_minutes':90,'failure_after_minutes':61,
     'expected':{'label':0,'machine_operating':True,'reason':'no_failure_in_horizon'}},
    {'id':'censored_future','operating':True,'future_minutes':30,'failure_after_minutes':None,
     'expected':{'label':None,'machine_operating':True,'reason':'insufficient_future'}},
    {'id':'maintenance_state','operating':False,'future_minutes':90,'failure_after_minutes':None,
     'expected':{'label':None,'machine_operating':False,'reason':'not_operating'}},
]
RULES=('Classify only the supplied synthetic pump observation. The horizon is the next 60 minutes. '
       'A non-operating machine has label null and reason not_operating. For an operating machine, a failure starting '
       'strictly after minute 0 and at or before minute 60 gives label 1 and reason failure_in_horizon. '
       'If no such failure has been observed and fewer than 60 future minutes are observed, label is null and reason '
       'insufficient_future. Otherwise label is 0 and reason no_failure_in_horizon. '
       'Return exactly one JSON object with keys label, machine_operating and reason. No additional fields or prose.')


def request_body(variant,case_index):
    """Change only the model identity across the paired inference bodies; use identical uncached deterministic controls."""
    if variant not in MODELS or type(case_index) is not int or not 0<=case_index<len(CASES):raise ValueError('Unknown comparison variant or case')
    case=CASES[case_index];inputs={k:case[k] for k in ['operating','future_minutes','failure_after_minutes']}
    return {'model':MODELS[variant],'messages':[{'role':'system','content':RULES},{'role':'user','content':json.dumps(inputs,sort_keys=True)}],
        'max_tokens':96,'temperature':0,'seed':7,'cache_prompt':False,'stream':True,'stream_options':{'include_usage':True}}


def unique_object(pairs):
    """Refuse duplicate JSON keys rather than permitting a later field to overwrite a contradictory model answer."""
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('Ambiguous JSON answer')
        value[key]=item
    return value


def score_answer(case_index,text):
    """Compare the actual answer with four hand-specified contract cases; failed answers remain scored and retained."""
    if type(case_index) is not int or not 0<=case_index<len(CASES) or not isinstance(text,str):raise ValueError('Invalid scoring input')
    text=text.strip()
    if text.startswith('```json\n') and text.endswith('\n```'):text=text[8:-4]
    try:value=json.loads(text,object_pairs_hook=unique_object)
    except ValueError:return {'correct':False,'reason':'invalid_json','answer':None}
    expected=CASES[case_index]['expected']
    if not isinstance(value,dict) or set(value)!=set(expected):return {'correct':False,'reason':'schema_mismatch','answer':value}
    equal=all(type(value[k]) is type(v) and value[k]==v for k,v in expected.items())
    return {'correct':equal,'reason':'correct' if equal else 'answer_mismatch','answer':value}


def summarize(rows):
    """Require the complete paired inventory and all generated replies, retaining incorrect answers in the measured denominator."""
    expected=Counter((phase,variant,repeat,case) for phase,repeats in [('warmup',[-1]),('measured',range(3))]
        for variant in MODELS for repeat in repeats for case in range(4))
    if Counter((r['phase'],r['variant'],r['repeat'],r['case_index']) for r in rows)!=expected:raise ValueError('Incomplete or duplicate paired comparison inventory')
    for row in rows:
        variant=row['variant'];usage=row['usage']
        if row['request_body']!=request_body(variant,row['case_index']) or row['model']!=MODELS[variant]:raise ValueError('Changed paired request or actual model identity')
        if row['status']!=200 or not row['complete'] or not row['text'] or row['cached_tokens']!=0:raise ValueError('Require actual complete uncached generation')
        if not all(type(usage.get(k)) is int and usage[k]>0 for k in ['prompt_tokens','completion_tokens','total_tokens']):raise ValueError('Missing provider token usage')
        if usage['total_tokens']!=usage['prompt_tokens']+usage['completion_tokens']:raise ValueError('Inconsistent provider token usage')
        if not all(type(row[k]) in {int,float} and isfinite(row[k]) for k in ['ttft_seconds','elapsed_seconds']) or not 0<row['ttft_seconds']<=row['elapsed_seconds']:
            raise ValueError('Invalid original request clocks')
        if row['score']!=score_answer(row['case_index'],row['text']):raise ValueError('A measured answer score was altered')
    results={}
    for variant,model in MODELS.items():
        selected=[r for r in rows if r['phase']=='measured' and r['variant']==variant]
        results[variant]={'model':model,'measured_requests':len(selected),'warmup_requests':4,
            'correct_contract_answers':sum(r['score']['correct'] for r in selected),'distinct_contract_cases':4,
            'median_full_seconds':median(r['elapsed_seconds'] for r in selected),'mean_full_seconds':mean(r['elapsed_seconds'] for r in selected),
            'median_ttft_seconds':median(r['ttft_seconds'] for r in selected),
            'input_tokens':sum(r['usage']['prompt_tokens'] for r in selected),'output_tokens':sum(r['usage']['completion_tokens'] for r in selected),
            'total_tokens':sum(r['usage']['total_tokens'] for r in selected)}
    return results


def prometheus_metrics(summary):
    """Export completed paired measurements with bounded variant labels, keeping this small protocol score distinct from ML accuracy."""
    if set(summary)!=set(MODELS):raise ValueError('Require exactly the two real model-artifact variants')
    mapping={'measured_requests':'measured_requests','contract_answers_correct':'correct_contract_answers','median_full_seconds':'median_full_seconds',
        'mean_full_seconds':'mean_full_seconds','median_ttft_seconds':'median_ttft_seconds','input_tokens':'input_tokens','output_tokens':'output_tokens','total_tokens':'total_tokens'}
    lines=[]
    for metric,field in mapping.items():
        name='iot_llm_ab_'+metric;lines.append('# TYPE '+name+' gauge')
        for variant in MODELS:
            value=summary[variant][field]
            if type(value) not in {int,float} or not isfinite(value) or value<0:raise ValueError('Invalid measured metric')
            lines.append(name+'{experiment="model-version",variant="'+variant+'"} '+format(value,'.17g'))
    lines.extend(['# TYPE iot_llm_ab_complete gauge','iot_llm_ab_complete{experiment="model-version"} 1'])
    return ('\n'.join(lines)+'\n').encode()
