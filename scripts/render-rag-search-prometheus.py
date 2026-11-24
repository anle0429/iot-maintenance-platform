"""Render private Prometheus additions for RAG Search and Drift API capacity."""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from infra.llm_observer_monitoring import pod_jobs

SEARCH_JOB = 'iot-rag-search'


def with_search_job(existing, *, drift_capacity_rules=None):
    """Reuse the existing MCP pod discovery identity and retain every previous job and global setting."""
    sources = [job for job in existing['scrape_configs'] if job['job_name'] == 'iot-agent-mcp']
    if len(sources) != 1:
        raise ValueError('Expected one private MCP pod discovery job')
    discovery, = sources[0]['kubernetes_sd_configs']
    job = pod_jobs(discovery['authorization']['credentials_file'], discovery['tls_config']['ca_file'])[0]
    search_discovery = job['kubernetes_sd_configs'][0]
    search_discovery['api_server'] = discovery['api_server']
    search_discovery['selectors'] = [{'role': 'pod', 'label': 'app.kubernetes.io/name=iot-rag-search'}]
    job['job_name'] = SEARCH_JOB
    for relabel in job['relabel_configs']:
        if relabel['source_labels'] == ['__meta_kubernetes_pod_container_name']:
            relabel['regex'] = 'search'
    job['metric_relabel_configs'][0]['regex'] = 'iot_rag_search_.*'
    current = [row for row in existing['scrape_configs'] if row['job_name'] == SEARCH_JOB]
    if current and current != [job]:
        raise ValueError('Existing RAG Search scrape job differs from the rendered definition')
    planned = deepcopy(existing)
    if not current:
        planned['scrape_configs'].append(job)
    if drift_capacity_rules is not None:
        rule_files = planned.setdefault('rule_files', [])
        if drift_capacity_rules not in rule_files:
            rule_files.append(drift_capacity_rules)
    return planned


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('configuration', type=Path, help='Existing private Prometheus JSON configuration')
    parser.add_argument('--drift-capacity-rules', help='Path to the installed rule file as seen by Prometheus')
    args = parser.parse_args()
    planned = with_search_job(json.loads(args.configuration.read_bytes()),
                              drift_capacity_rules=args.drift_capacity_rules)
    print(json.dumps(planned, indent=2))
