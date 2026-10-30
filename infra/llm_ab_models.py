"""Plan a same-GPU two-model deployment and preserve existing monitoring while adding explicit model-version series."""
from copy import deepcopy

MODELS=('iot-qwen2.5-7b','iot-qwen2.5-1.5b')


def monitoring_configuration(existing):
    """Keep original jobs and labels; supply the pinned router's required model query and two distinct new jobs."""
    planned=deepcopy(existing);jobs=planned['scrape_configs'];names=[v['job_name'] for v in jobs]
    if names.count('iot-llm-model')!=1 or {'iot-llm-model-small','iot-llm-ab'}&set(names):raise ValueError('Inspect an unexpected or already extended monitoring configuration')
    primary=next(v for v in jobs if v['job_name']=='iot-llm-model')
    if primary.get('params') not in [None,{}]:raise ValueError('Preserve an unexpected model parameter')
    primary['params']={'model':[MODELS[0]]}
    small=deepcopy(primary);small['job_name']='iot-llm-model-small';small['params']={'model':[MODELS[1]]};jobs.append(small)
    jobs.append({'job_name':'iot-llm-ab','honor_labels':True,'scrape_interval':'2s','scrape_timeout':'1s','sample_limit':2000,
        'static_configs':[{'targets':['127.0.0.1:19092']}],
        'metric_relabel_configs':[{'source_labels':['__name__'],'regex':'iot_llm_ab_.*','action':'keep'}]})
    restored=deepcopy(planned);restored['scrape_configs']=restored['scrape_configs'][:-2]
    del next(v for v in restored['scrape_configs'] if v['job_name']=='iot-llm-model')['params']
    original=deepcopy(existing)
    if next(v for v in original['scrape_configs'] if v['job_name']=='iot-llm-model').get('params')=={}:
        del next(v for v in original['scrape_configs'] if v['job_name']=='iot-llm-model')['params']
    if restored!=original:raise ValueError('An unrelated monitoring setting changed')
    return planned
