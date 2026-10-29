"""Describe separate per-Pod LLM/MCP/host scrape jobs without changing the existing ML monitoring configuration."""
from copy import deepcopy


def pod_jobs(token_file,ca_file):
    """Discover actual application containers and ports; retain unhealthy running Pods so failures do not disappear."""
    specifications=[
        ('iot-agent-mcp','iot-agents','app.kubernetes.io/instance in (iot-retrieval-mcp,iot-drift-mcp)','mcp','8080','iot_mcp_.*'),
        ('iot-llm-observer','iot-llm','app.kubernetes.io/instance=iot-llm-observer','observer','8081','iot_.*'),
        ('iot-llm-model','iot-llm','app.kubernetes.io/instance=iot-model-gpu','model','8080','llamacpp:.*'),
        ('iot-llm-gpu-host','iot-llm','app.kubernetes.io/instance=iot-gpu-host-telemetry','exporter','9100',
         'node_cpu_seconds_total|node_memory_MemAvailable_bytes|node_memory_MemTotal_bytes|node_filesystem_avail_bytes|node_filesystem_size_bytes|node_disk_read_bytes_total|node_disk_written_bytes_total|node_network_receive_bytes_total|node_network_transmit_bytes_total|node_scrape_collector_success|node_exporter_build_info'),
    ]
    jobs=[]
    for name,namespace,selector,container,port,metrics in specifications:
        discovery={'role':'pod','api_server':'https://10.53.0.1:443','authorization':{'credentials_file':str(token_file)},
            'tls_config':{'ca_file':str(ca_file)},'namespaces':{'names':[namespace]},'selectors':[{'role':'pod','label':selector}]}
        relabel=[{'source_labels':['__meta_kubernetes_pod_phase'],'regex':'Running','action':'keep'},
            {'source_labels':['__meta_kubernetes_pod_container_init'],'regex':'false','action':'keep'},
            {'source_labels':['__meta_kubernetes_pod_container_name'],'regex':container,'action':'keep'},
            {'source_labels':['__meta_kubernetes_pod_container_port_number'],'regex':port,'action':'keep'}]
        for source,target in [('namespace','namespace'),('name','pod'),('uid','pod_uid'),('node_name','host'),('ready','pod_ready')]:
            source='__meta_kubernetes_namespace' if source=='namespace' else '__meta_kubernetes_pod_'+source
            relabel.append({'source_labels':[source],'target_label':target})
        relabel.append({'source_labels':['__meta_kubernetes_pod_label_app_kubernetes_io_instance'],'target_label':'service'})
        jobs.append({'job_name':name,'scrape_interval':'2s','scrape_timeout':'1s','metrics_path':'/metrics','sample_limit':5000,
            'kubernetes_sd_configs':[discovery],'relabel_configs':relabel,
            'metric_relabel_configs':[{'source_labels':['__name__'],'regex':metrics,'action':'keep'}]})
    return jobs


def extended_configuration(existing,token_file,ca_file):
    """Append four distinct jobs while preserving every original scrape job and global setting byte-for-value."""
    planned=deepcopy(existing);additions=pod_jobs(token_file,ca_file)
    old={v['job_name'] for v in planned['scrape_configs']};new={v['job_name'] for v in additions}
    if old&new:raise ValueError('Inspect the existing LLM monitoring jobs before replacing any configuration')
    planned['scrape_configs'].extend(additions)
    restored=deepcopy(planned);restored['scrape_configs']=restored['scrape_configs'][:-len(additions)]
    if restored!=existing:raise ValueError('The monitoring extension changed an original setting')
    return planned
