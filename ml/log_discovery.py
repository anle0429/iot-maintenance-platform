"""Render node-local, namespace-scoped Kubernetes log discovery while retaining the existing host log pipeline."""
from pathlib import PurePosixPath
import json,re
from ml.log_monitoring import absolute,host_alias,render


def application_discovery(token_file,ca_file,node,host='vm-linux',collection='native-28-auto'):
    """Watch public Pod metadata with a scoped token; derive CRI paths from namespace/name/UID/container."""
    token=absolute(token_file);ca=absolute(ca_file);host_alias(host);host_alias(collection)
    if not isinstance(node,str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}',node):raise ValueError('Invalid observed node name')
    if token==ca:raise ValueError('Token and public CA require separate files')
    q=json.dumps
    rules=[]
    def rule(**fields):rules.append('  rule {\n'+''.join('    '+n+' = '+q(v)+'\n' for n,v in fields.items())+'  }')
    rule(source_labels=['__meta_kubernetes_pod_container_init'],regex='false',action='keep')
    rule(source_labels=['__meta_kubernetes_pod_phase'],regex='Running',action='keep')
    for label,value,service in [('app_kubernetes_io_instance','iot-feature-api','feature-api'),('app_kubernetes_io_instance','iot-drift-api','drift-api'),('app_kubernetes_io_instance','iot-ab-serve-jenkins-8','ab-api'),('serving_kserve_io_inferenceservice','iot-pump-serve-jenkins-8','predictor'),('app','iot-log-discovery-probe','log-probe')]:
        rule(source_labels=['__meta_kubernetes_pod_label_'+label],regex=value,target_label='service',replacement=service)
    rule(source_labels=['service'],regex='feature-api|drift-api|ab-api|predictor|log-probe',action='keep')
    rule(source_labels=['__meta_kubernetes_pod_container_name'],regex='api|kserve-container|probe',action='keep')
    for source,target in [('namespace','namespace'),('pod_name','pod'),('pod_uid','pod_uid'),('pod_container_name','container')]:rule(source_labels=['__meta_kubernetes_'+source],target_label=target)
    rule(source_labels=['namespace','pod','pod_uid','container'],separator=';',regex='(.+);(.+);(.+);(.+)',target_label='__path__',replacement='/var/log/pods/$1_$2_$3/$4/*.log')
    for label,value in [('host',host),('collection',collection),('source','cri')]:rule(target_label=label,replacement=value)
    rule(action='labelkeep',regex='__path__|host|collection|source|service|namespace|pod|pod_uid|container')
    return f'''discovery.kubernetes "applications" {{
  role = "pod"
  api_server = "https://10.53.0.1:443"
  bearer_token_file = {q(str(token))}
  tls_config {{ ca_file = {q(str(ca))} }}
  namespaces {{ names = ["iot-features", "iot-serving"] }}
  selectors {{
    role = "pod"
    field = {q('spec.nodeName='+node)}
  }}
}}
discovery.relabel "applications" {{
  targets = discovery.kubernetes.applications.targets
{chr(10).join(rules)}
}}
local.file_match "applications" {{
  path_targets = discovery.relabel.applications.output
  sync_period = "5s"
}}
loki.source.file "applications_auto" {{
  targets = local.file_match.applications.targets
  forward_to = [loki.process.applications.receiver]
}}
'''


def render_discovery(runtime_root,project_root,observed,token_file,ca_file,node,host='vm-linux',collection='native-28-auto'):
    """Keep original host parsing/Loki contract; replace fixed application paths with authenticated discovery."""
    docs=render(runtime_root,project_root,observed,host,collection)
    before,rest=docs['alloy'].split('loki.source.file "applications" {',1)
    _,after=rest.split('loki.process "applications" {',1)
    docs['alloy']=before+application_discovery(token_file,ca_file,node,host,collection)+'loki.process "applications" {'+after
    docs['dashboard']['description']='Three host stdout files and node-local Kubernetes-discovered CRI application logs. Scoped public Pod metadata; no Secrets API. Original timestamps; bounded probe demonstrates Pod replacement.'
    docs['sources']={'host_files':docs['sources']['host_files'],'discovery':{'namespaces':['iot-features','iot-serving'],'node':node,'services':['feature-api','drift-api','ab-api','predictor','log-probe'],'path_template':'/var/log/pods/<namespace>_<pod>_<uid>/<container>/*.log','private_token_file':str(absolute(token_file)),'public_ca_file':str(absolute(ca_file))}}
    return docs


def probe_deployment(batch):
    """Emit twenty known JSON lines once from a real bounded Pod; identity comes from the downward API."""
    if batch not in ['first','second']:raise ValueError('Require first or second controlled batch')
    program="import json,os,time;[print(json.dumps({'experiment':'iot-log-discovery-28','batch':os.environ['BATCH'],'sequence':i,'pod_uid':os.environ['POD_UID']}),flush=True) for i in range(20)];time.sleep(10800)"
    return {'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':'iot-log-discovery-probe','namespace':'iot-features'},'spec':{'replicas':1,'strategy':{'type':'Recreate'},'selector':{'matchLabels':{'app':'iot-log-discovery-probe'}},'template':{'metadata':{'labels':{'app':'iot-log-discovery-probe'}},'spec':{'automountServiceAccountToken':False,'securityContext':{'runAsNonRoot':True,'runAsUser':10001,'seccompProfile':{'type':'RuntimeDefault'}},'containers':[{'name':'probe','image':'iot-coursework-predictor:observe-jenkins-2','imagePullPolicy':'Never','command':['python','-c',program],'env':[{'name':'BATCH','value':batch},{'name':'POD_UID','valueFrom':{'fieldRef':{'fieldPath':'metadata.uid'}}}],'resources':{'requests':{'cpu':'10m','memory':'32Mi'},'limits':{'cpu':'100m','memory':'128Mi'}},'securityContext':{'readOnlyRootFilesystem':True,'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}}}]}}}}
