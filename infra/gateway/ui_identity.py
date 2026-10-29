"""Guard the kagent UI's supported identity-header forwarding without changing controller or global model settings."""
from copy import deepcopy


def ui_identity_patch(deployment):
    """Add only the documented UI environment setting, with UID/current-value tests against concurrent changes."""
    metadata=deployment['metadata'];pod=deployment['spec']['template']['spec'];containers=pod['containers']
    if (metadata.get('name')!='kagent-ui' or metadata.get('namespace')!='iot-agents' or not metadata.get('uid')
            or len(containers)!=1 or containers[0]['name']!='ui' or containers[0]['image']!='ghcr.io/kagent-dev/kagent/ui:0.10.3'):
        raise ValueError('Preserve a different kagent UI or image version')
    key='KAGENT_ADDITIONAL_FORWARDED_HEADERS';before=containers[0].get('env',[]);matching=[row for row in before if row['name']==key]
    if len(matching)>1 or matching and matching[0]!={'name':key,'value':'X-User-Id'}:raise ValueError('Preserve another configured UI forwarding policy')
    if matching:return []
    after=deepcopy(before)+[{'name':key,'value':'X-User-Id'}]
    patch=[{'op':'test','path':'/metadata/uid','value':metadata['uid']}]
    if 'env' in containers[0]:patch.append({'op':'test','path':'/spec/template/spec/containers/0/env','value':before})
    patch.append({'op':'add' if 'env' not in containers[0] else 'replace','path':'/spec/template/spec/containers/0/env','value':after})
    return patch
