"""Plan narrow steady-state monitoring budgets while preserving original short-experiment configuration sources."""
from copy import deepcopy


def monitoring_configuration(existing):
    """Change only the observer scrape cadence/timeout, preserving every target, credential reference and other job."""
    planned = deepcopy(existing)
    jobs = [v for v in planned['scrape_configs'] if v['job_name'] == 'iot-llm-observer']
    if len(jobs) != 1: raise ValueError('Require one existing observer scrape job')
    job = jobs[0]
    if (job['scrape_interval'], job['scrape_timeout']) not in {('2s', '1s'), ('15s', '10s')}:
        raise ValueError('Inspect an unexpected monitoring budget before changing it')
    job['scrape_interval'] = '15s'; job['scrape_timeout'] = '10s'
    return planned


def observer_deployment(existing):
    """Adjust only the observer's actual HTTP probe timeouts; retain backend checks, failures and all workload settings."""
    if existing['metadata']['name'] != 'iot-llm-observer' or existing['metadata']['namespace'] != 'iot-llm':
        raise ValueError('Require the retained observer deployment')
    planned = deepcopy(existing)
    containers = [v for v in planned['spec']['template']['spec']['containers'] if v['name'] == 'observer']
    if len(containers) != 1: raise ValueError('Require exactly one observer container')
    container = containers[0]
    for name, path, original_timeout in [('livenessProbe', '/health/live', 1), ('readinessProbe', '/health/ready', 3)]:
        probe = container[name]
        if probe['httpGet'] != {'path': path, 'port': 'observe', 'scheme': 'HTTP'} or probe.get('timeoutSeconds', 1) not in {original_timeout, 5}:
            raise ValueError('Preserve the native HTTP checks and inspect an unexpected probe budget')
        probe['timeoutSeconds'] = 5
    return planned
