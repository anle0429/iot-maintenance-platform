"""Plan isolated retrieval-agent variants and summarize paired native tool-grounded answers."""
from copy import deepcopy
from math import isfinite
from statistics import median

VARIANTS = {'a': {'agent': 'iot-ab-retrieval-a', 'model_config': 'iot-ab-model-a', 'temperature': '0'},
            'b': {'agent': 'iot-ab-retrieval-b', 'model_config': 'iot-ab-model-b', 'temperature': '0.3'}}


def configuration_plan(model, agent, policy):
    """Change temperature in two new ModelConfigs while preserving the production agent, model, tools and sandbox policy."""
    spec = model['spec']; baseline = agent['spec']
    if (model['metadata']['name'] != 'default-model-config' or spec['model'] != 'iot-qwen2.5-7b'
            or spec['openAI']['temperature'] != '0' or spec['openAI']['seed'] != 7 or spec['openAI']['maxTokens'] != 768
            or baseline['version'] != policy['version'] or baseline['declarative']['systemMessage'] != policy['prompts']['retrieval']
            or baseline['declarative']['modelConfig'] != 'default-model-config'
            or baseline['declarative']['executeCodeBlocks'] is not False
            or baseline['substrate']['workerPoolRef']['name'] != 'iot-retrieval-pool'):
        raise ValueError('Require the current reviewed retrieval policy and shared 7B model configuration')
    objects = []
    for variant, settings in VARIANTS.items():
        model_spec = deepcopy(spec); model_spec['openAI']['temperature'] = settings['temperature']
        agent_spec = deepcopy(baseline); agent_spec['declarative']['modelConfig'] = settings['model_config']
        for kind, name, value in [('ModelConfig', settings['model_config'], model_spec), ('SandboxAgent', settings['agent'], agent_spec)]:
            objects.append({'apiVersion': 'kagent.dev/v1alpha2', 'kind': kind,
                'metadata': {'name': name, 'namespace': 'iot-agents', 'labels': {'iot.coursework/experiment': 'agent-config-30a', 'iot.coursework/variant': variant}}, 'spec': value})
    return objects


def comparison_summary(rows, machines):
    """Keep failed answers in a complete paired cohort; a small fixed-seed comparison cannot establish a general quality ranking."""
    expected = {(v, m) for v in VARIANTS for m in machines}
    if len(machines) != 3 or len(set(machines)) != 3 or len(rows) != len(expected) or {(r['variant'], r['machine_id']) for r in rows} != expected:
        raise ValueError('Require exactly three distinct paired machines and both variants')
    for row in rows:
        if (type(row['passed']) is not bool or type(row['elapsed_seconds']) not in {int, float}
                or not isfinite(row['elapsed_seconds']) or row['elapsed_seconds'] <= 0
                or not row['context_id'] or not row['request_id']):
            raise ValueError('Require original positive request clocks and attributable paired outcomes')
    return {v: {'requests': 3, 'tool_grounded_answers': sum(r['passed'] for r in rows if r['variant'] == v),
                'median_complete_seconds': median(r['elapsed_seconds'] for r in rows if r['variant'] == v),
                'temperature': settings['temperature'], 'model': 'iot-qwen2.5-7b'} for v, settings in VARIANTS.items()}
