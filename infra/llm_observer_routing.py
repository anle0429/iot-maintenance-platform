"""Plan an exact private trace route while preserving the established model router and agent business configuration."""
from copy import deepcopy

TRACE_PATH='/iot-telemetry/v1/traces'
TRACE_CLUSTER='iot_llm_private_traces'
TRACE_ENV={
    'OTEL_TRACING_ENABLED':'true',
    'OTEL_LOGGING_ENABLED':'false',
    'OTEL_EXPORTER_OTLP_TRACES_PROTOCOL':'http/protobuf',
    'OTEL_EXPORTER_OTLP_TRACES_ENDPOINT':'http://iot-router-epp.iot-llm.svc.cluster.local:8081'+TRACE_PATH,
    'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT':'false',
}


def private_trace_route(config):
    """Add one exact POST route and fixed private cluster; refuse an ambiguous or already modified router."""
    planned=deepcopy(config)
    resources=planned['static_resources'];listeners=resources['listeners'];clusters=resources['clusters']
    if any(v['name']==TRACE_CLUSTER for v in clusters):raise ValueError('Preserve an existing observation cluster')
    selected=[v for v in listeners if v['address']['socket_address']['port_value']==8081]
    if len(selected)!=1:raise ValueError('Require one retained model listener')
    chains=selected[0]['filter_chains']
    if len(chains)!=1:raise ValueError('Require the retained single filter chain')
    filters=[v for v in chains[0]['filters'] if v['name']=='envoy.filters.network.http_connection_manager']
    if len(filters)!=1:raise ValueError('Require one retained HTTP connection manager')
    manager=filters[0]['typed_config'];hosts=manager['route_config']['virtual_hosts']
    if len(hosts)!=1 or hosts[0]['domains']!=['*']:raise ValueError('Preserve a different virtual host layout')
    routes=hosts[0]['routes']
    if len(routes)!=1 or routes[0]['match']!={'prefix':'/'} or routes[0]['route']['cluster']!='original_destination_cluster':
        raise ValueError('Preserve different existing model routes')
    if not any(v['name']=='envoy.filters.http.ext_proc' for v in manager['http_filters']):raise ValueError('Require the retained model picker')
    routes.insert(0,{
        'match':{'path':TRACE_PATH,'headers':[{'name':':method','string_match':{'exact':'POST'}}]},
        'route':{'cluster':TRACE_CLUSTER,'prefix_rewrite':'/v1/traces','timeout':'15s'},
        'typed_per_filter_config':{'envoy.filters.http.ext_proc':{
            '@type':'type.googleapis.com/envoy.extensions.filters.http.ext_proc.v3.ExtProcPerRoute','disabled':True}},
    })
    clusters.append({'name':TRACE_CLUSTER,'type':'STRICT_DNS','connect_timeout':'2s','dns_lookup_family':'V4_ONLY',
        'load_assignment':{'cluster_name':TRACE_CLUSTER,'endpoints':[{'lb_endpoints':[{'endpoint':{'address':{'socket_address':{
            'address':'iot-llm-observer.iot-llm.svc.cluster.local','port_value':8081}}}}]}]}})
    # Remove exactly the two additions to prove every original routing field is preserved.
    restored=deepcopy(planned);restored['static_resources']['clusters'].pop()
    restored_listener=next(v for v in restored['static_resources']['listeners'] if v['address']['socket_address']['port_value']==8081)
    restored_manager=next(v['typed_config'] for v in restored_listener['filter_chains'][0]['filters'] if v['name']=='envoy.filters.network.http_connection_manager')
    restored_manager['route_config']['virtual_hosts'][0]['routes'].pop(0)
    if restored!=config:raise ValueError('The trace bridge changed existing routing')
    return planned


def controller_trace_environment(environment):
    """Preserve unrelated controller settings and add only supported private trace settings with content capture disabled."""
    values=deepcopy(environment)
    names=[v['name'] for v in values]
    if len(names)!=len(set(names)):raise ValueError('Duplicate controller environment keys')
    for key,value in TRACE_ENV.items():
        existing=[v for v in values if v['name']==key]
        if existing and existing!=[{'name':key,'value':value}]:raise ValueError('Preserve a different existing trace configuration')
        if not existing:values.append({'name':key,'value':value})
    return values
