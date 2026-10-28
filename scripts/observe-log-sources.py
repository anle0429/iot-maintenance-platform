"""Observe existing coursework stdout and the active API Pod log identities; never change services, data or permissions."""
import argparse,json,re,subprocess
from pathlib import Path


def observe(root):
    """Resolve process stdout and actual Service selectors so a new capture does not reuse an obsolete Pod UID."""
    sources=[];pods=[]
    for service in ['node-exporter','prometheus','grafana']:
        pid=int(subprocess.check_output(['systemctl','show','iot-'+service+'-28','-p','MainPID','--value'],text=True))
        if pid<=0:raise ValueError('Expected the running coursework '+service+' unit')
        path=(Path('/proc')/str(pid)/'fd/1').resolve(strict=True)
        if path!=root/'outputs/linux-monitoring-28'/(service+'.txt'):raise ValueError('Unexpected host stdout path')
        key='t' if service=='grafana' else 'time'
        with path.open() as stream:first=stream.readline()
        if not re.search(r'(?:^|\s)'+key+r'=\d{4}-\d\d-\d\dT',first):raise ValueError('Unverified host timestamp format')
        sources.append({'service':service,'path':str(path),'time_key':key})
    get=lambda *args:json.loads(subprocess.check_output(['k3s','kubectl',*args,'-o','json']))
    deployment=get('get','deployment','iot-feature-api','-n','iot-features')
    api=next(c for c in deployment['spec']['template']['spec']['containers'] if c['name']=='api')
    url=next(e['value'] for e in api['env'] if e['name']=='IOT_KSERVE_URL')
    target=re.fullmatch(r'http://([a-z0-9-]+)\.iot-serving\.svc\.cluster\.local',url)
    if not target:raise ValueError('The active predictor is not the declared private coursework service')
    for namespace,name,service in [('iot-serving',target[1],'predictor'),('iot-features','iot-feature-api','feature-api')]:
        selected=get('get','service',name,'-n',namespace)['spec']['selector']
        if not selected:raise ValueError('Application Service has no selector')
        selector=','.join(k+'='+v for k,v in sorted(selected.items()))
        for pod in get('get','pods','-n',namespace,'-l',selector)['items']:
            if pod['metadata'].get('deletionTimestamp'):continue
            for container in pod['status'].get('containerStatuses',[]):
                if 'running' not in container.get('state',{}):continue
                directory=Path('/var/log/pods')/(namespace+'_'+pod['metadata']['name']+'_'+pod['metadata']['uid'])/container['name']
                files=[p for p in directory.glob('*.log') if p.stem.isdigit()]
                if not files:raise ValueError('Current application has no observed CRI file')
                current=max(files,key=lambda p:int(p.stem))
                pods.append({'namespace':namespace,'pod':pod['metadata']['name'],'uid':pod['metadata']['uid'],
                    'container':container['name'],'rotation':int(current.stem),'service':service,'path':str(current)})
    if {p['service'] for p in pods}!={'predictor','feature-api'}:raise ValueError('Both application log sources must exist')
    return {'sources':sources,'pods':pods}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project-root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=observe(args.project_root.resolve());args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps({'observed_sources':len(result['sources'])+len(result['pods']),'services_started':False,'output':str(args.output)}))
