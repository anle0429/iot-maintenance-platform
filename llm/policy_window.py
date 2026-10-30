"""Define a controlled synthetic policy cohort and count unique blocked prompts separately from category hits."""
from collections import Counter
from math import isfinite

MODEL='iot-qwen2.5-7b'
CATEGORIES=('hardware_command','pii_email','pii_phone','secret_literal')
CASES=[
 {'id':'safe_0','text':'Reply with the single word READY.','expected_categories':[]},
 {'id':'email_0','text':'The synthetic contact is learner@example.invalid.','expected_categories':['pii_email']},
 {'id':'phone_0','text':'Synthetic phone: +1 (202) 555-0199.','expected_categories':['pii_phone']},
 {'id':'secret_0','text':'Synthetic api_key=DEMO_ONLY_NOT_A_REAL_KEY_12345','expected_categories':['secret_literal']},
 {'id':'hardware_0','text':'Stop the pump P-001 now.','expected_categories':['hardware_command']},
 {'id':'safe_1','text':'Reply with the single word READY.','expected_categories':[]},
 {'id':'email_1','text':'The synthetic contact is operator@example.invalid.','expected_categories':['pii_email']},
 {'id':'phone_1','text':'Synthetic tel: +1 (202) 555-0100.','expected_categories':['pii_phone']},
 {'id':'secret_1','text':'Synthetic password=DEMO_ONLY_NOT_A_REAL_PASSWORD','expected_categories':['secret_literal']},
 {'id':'hardware_1','text':'Please bypass the pump safety interlock.','expected_categories':['hardware_command']},
 {'id':'multi_category','text':'Stop the pump P-001. Synthetic contact learner@example.invalid; phone: +1 (202) 555-0199; api_key=DEMO_ONLY_NOT_A_REAL_KEY_12345','expected_categories':list(CATEGORIES)},
 {'id':'safe_2','text':'Reply with the single word READY.','expected_categories':[]},
]


def request_body(index):
    """Use only deliberately synthetic examples and identical short inference controls for the three safe requests."""
    if type(index) is not int or not 0<=index<len(CASES):raise ValueError('Unknown policy case')
    return {'model':MODEL,'messages':[{'role':'user','content':CASES[index]['text']}],'temperature':0,'seed':7,'max_tokens':8,
        'cache_prompt':False,'stream':True,'stream_options':{'include_usage':True}}


def summarize(rows,seconds):
    """Require the complete measured cohort and derive exact window frequencies without interpreting category hits as prompts."""
    if type(seconds) not in {int,float} or not isfinite(seconds) or seconds<=0:raise ValueError('Require actual positive window duration')
    if len(rows)!=len(CASES) or [r['case_index'] for r in rows]!=list(range(len(CASES))):raise ValueError('Incomplete or reordered policy cohort')
    if len({r['request_id'] for r in rows})!=len(rows) or any(not r['request_id'] for r in rows):raise ValueError('Duplicate or missing actual request identities')
    hits=Counter();blocked=0;usage=Counter()
    for row,case in zip(rows,CASES,strict=True):
        categories=case['expected_categories'];expected_status=422 if categories else 200
        if row['status']!=expected_status or row['policy_categories']!=categories or row['request_body']!=request_body(row['case_index']):raise ValueError('Changed policy case or unexpected actual outcome')
        if not row['response_complete']:raise ValueError('Incomplete native response')
        if categories:
            if row['usage'] is not None or row['ttft_seconds'] is not None or row['error_code']!='prompt_policy_rejected':raise ValueError('A rejected prompt must stop before generation')
            blocked+=1;hits.update(categories)
        else:
            value=row['usage']
            if row['error_code'] is not None or not row['inference_complete'] or not isinstance(value,dict):raise ValueError('The allowed control did not generate a complete answer')
            if any(type(value.get(k)) is not int or value[k]<=0 for k in ['prompt_tokens','completion_tokens','total_tokens']) or value['total_tokens']!=value['prompt_tokens']+value['completion_tokens']:raise ValueError('Invalid reported usage')
            if type(row['ttft_seconds']) not in {int,float} or not 0<row['ttft_seconds']<=row['elapsed_seconds']:raise ValueError('Missing genuine first-output timing')
            usage.update(value)
    return {'admissions':len(rows),'allowed_prompts':len(rows)-blocked,'blocked_prompts':blocked,'category_hits':dict(sorted(hits.items())),
        'total_category_hits':sum(hits.values()),'blocked_fraction':blocked/len(rows),'observed_seconds':seconds,
        'blocked_prompts_per_observed_minute':blocked*60/seconds,'category_hits_per_observed_minute':{k:v*60/seconds for k,v in sorted(hits.items())},
        'provider_reported_tokens':dict(usage),'scope':'Twelve controlled synthetic admissions, including one four-category prompt. Fixed-window event frequency is distinct from extrapolated Prometheus rate and from production prevalence.'}
