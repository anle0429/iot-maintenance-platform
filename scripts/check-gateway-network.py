"""Compare one admitted and one unlabelled native Pod without sending credentials or model requests."""
import argparse
from ipaddress import ip_address
import json
from pathlib import Path
import time

from importlib.util import module_from_spec, spec_from_file_location

spec=spec_from_file_location('native_smoke',Path(__file__).with_name('native-gateway-driver.py'))
native=module_from_spec(spec)
spec.loader.exec_module(native)
PROBE='''import json,socket,sys
address,port=sys.argv[1],int(sys.argv[2])
try:
    with socket.create_connection((address,port),timeout=3):
        result={'connected':True}
except OSError as error:
    result={'connected':False,'error':type(error).__name__,'errno':error.errno}
print(json.dumps(result))
'''


def run(out, name):
    """Run bounded network probes using the already installed drift MCP image."""
    namespace='iot-agents'
    gateway=native.read('service','iot-agentgateway-candidate','-n',namespace)
    address=gateway['spec']['clusterIP']
    run_name=gateway['metadata']['annotations'][native.RUN_ANNOTATION]
    assert gateway['spec']['type']=='ClusterIP' and ip_address(address).is_private
    native.ready_endpoints(namespace,'iot-agentgateway-candidate')
    drift=native.read('deployment','iot-drift-mcp','-n',namespace)
    image=drift['spec']['template']['spec']['containers'][0]['image']
    out.mkdir(exist_ok=False,mode=0o700)
    cases={}
    for case,allowed in [('unlabelled',False),('admitted',True)]:
        podname=name+'-'+case
        assert not native.command('get','pod',podname,'-n',namespace,'--ignore-not-found','-o','name').strip()
        labels={'iot.coursework/network-validation':name}
        if allowed:labels['iot.coursework/agentgateway-probe']=run_name
        resource={'apiVersion':'v1','kind':'Pod','metadata':{'name':podname,'namespace':namespace,'labels':labels},
                  'spec':{'nodeName':'iot-cw-vm','restartPolicy':'Never','activeDeadlineSeconds':45,
                          'automountServiceAccountToken':False,'enableServiceLinks':False,
                          'securityContext':{'runAsNonRoot':True,'runAsUser':10001,'runAsGroup':10001,
                                             'seccompProfile':{'type':'RuntimeDefault'}},
                          'containers':[{'name':'probe','image':image,'imagePullPolicy':'Never',
                            'command':['python','-B','-c',PROBE,address,'18080'],
                            'resources':{'requests':{'cpu':'25m','memory':'32Mi'},'limits':{'cpu':'100m','memory':'96Mi'}},
                            'securityContext':{'readOnlyRootFilesystem':True,'allowPrivilegeEscalation':False,
                                               'capabilities':{'drop':['ALL']}}}]}}
        created=json.loads(native.command('create','-f','-','-o','json',payload=json.dumps(resource).encode()))
        try:
            end=time.monotonic()+50
            while time.monotonic()<end:
                pod=native.read('pod',podname,'-n',namespace)
                if pod['status']['phase'] in ('Succeeded','Failed'):break
                time.sleep(1)
            native.save(out/(case+'-pod.json'),pod)
            assert pod['metadata']['uid']==created['metadata']['uid'] and pod['status']['phase']=='Succeeded'
            raw=native.command('logs',podname,'-n',namespace)
            (out/(case+'.stdout')).write_bytes(raw)
            actual=json.loads(raw)
            cases[case]=actual
            assert actual['connected']==allowed,(case,actual)
        finally:
            current=native.read('pod',podname,'-n',namespace)
            assert current['metadata']['uid']==created['metadata']['uid']
            native.command('delete','pod',podname,'-n',namespace,'--timeout=20s')
    native.save(out/'verification.json',{'passed':True,'cases':cases,'scope':'ClusterIP TCP ingress from two native Pods; no model request, Internet port or general bypass claim.'})


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output',type=Path,required=True)
    cli.add_argument('--name',required=True)
    args=cli.parse_args()
    run(args.output,args.name)
