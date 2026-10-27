"""Connect only the private pod network to the existing MLflow Docker server and verify a completed data run."""
import argparse,ipaddress,json,os,re,subprocess,sys,time,shutil
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.training_contract import digest,save


def run(args):
    """Resolve actual addresses, retain prior configuration, and require a native non-root Kubernetes HTTP oracle."""
    if os.name!='posix' or os.geteuid()!=0 or not re.fullmatch('[a-z][a-z0-9-]{2,30}',args.run):raise ValueError('Use root on the prepared Linux VM and a safe run name')
    out=ROOT/'outputs/kfp-registry-route'/args.run;out.mkdir(parents=True,exist_ok=False)
    k=['/usr/local/bin/k3s','kubectl']
    def call(name,command):
        with (out/(name+'.txt')).open('w') as stream:subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=180)
    addresses=json.loads(subprocess.check_output(['ip','-j','-4','address','show','dev','cni0'],text=True))
    pod_address=next(a['local'] for interface in addresses for a in interface['addr_info'] if a['family']=='inet')
    server=json.loads(subprocess.check_output(['docker','inspect','iot-mlflow-tracking-1'],text=True))[0]
    upstream=server['NetworkSettings']['Networks']['iot-mlflow_default']['IPAddress']
    if not ipaddress.ip_address(pod_address).is_private or not ipaddress.ip_address(upstream).is_private:raise ValueError('Require private pod and Docker addresses')
    config=Path('/etc/iot-coursework');config.mkdir(exist_ok=True)
    env=config/'mlflow-bridge.env'
    if env.exists():shutil.copyfile(env,out/'previous-upstream.env.txt')
    env.write_text('IOT_MLFLOW_UPSTREAM='+upstream+':5000\n');env.chmod(0o644)
    for suffix in ['socket','service']:
        source=ROOT/('infra/kfp/iot-mlflow-bridge.'+suffix);target=Path('/etc/systemd/system')/source.name
        if target.exists():shutil.copyfile(target,out/('previous-'+source.name+'.txt'))
        text=source.read_text()
        if suffix=='socket':text=re.sub(r'^ListenStream=.*$', 'ListenStream='+pod_address+':15002',text,flags=re.M)
        target.write_text(text);target.chmod(0o644)
    call('daemon-reload',['systemctl','daemon-reload']);call('socket-start',['systemctl','start','iot-mlflow-bridge.socket'])
    call('proxy-restart',['systemctl','restart','iot-mlflow-bridge.service'])
    existing=subprocess.run(k+['get','service','tracking','-n','kubeflow','-o','json'],capture_output=True,text=True)
    if existing.returncode==0 and json.loads(existing.stdout)['metadata'].get('labels',{}).get('app')!='iot-private-registry':raise ValueError('Preserve unrelated tracking service')
    service={'apiVersion':'v1','kind':'Service','metadata':{'name':'tracking','namespace':'kubeflow','labels':{'app':'iot-private-registry'}},'spec':{'ports':[{'name':'http','port':5000,'targetPort':15002}]}}
    endpoint={'apiVersion':'discovery.k8s.io/v1','kind':'EndpointSlice','metadata':{'name':'iot-private-registry','namespace':'kubeflow','labels':{'kubernetes.io/service-name':'tracking','app':'iot-private-registry'}},'addressType':'IPv4','ports':[{'name':'http','port':15002,'protocol':'TCP'}],'endpoints':[{'addresses':[pod_address],'conditions':{'ready':True}}]}
    manifest=out/'routing.json';save(manifest,{'apiVersion':'v1','kind':'List','items':[service,endpoint]});call('route-apply',k+['apply','-f',str(manifest)])
    code="import urllib.request,json;assert urllib.request.urlopen('http://tracking:5000/health',timeout=5).status==200;v=json.load(urllib.request.urlopen('http://tracking:5000/api/2.0/mlflow/runs/get?run_id="+args.data_run+"',timeout=5));assert v['run']['info']['status']=='FINISHED';print('PASS native private registry health and completed data run')"
    probe={'apiVersion':'v1','kind':'Pod','metadata':{'name':'iot-registry-route-'+args.run,'namespace':'kubeflow'},'spec':{'restartPolicy':'Never','activeDeadlineSeconds':120,'automountServiceAccountToken':False,
        'securityContext':{'runAsUser':10001,'runAsGroup':10001,'runAsNonRoot':True,'seccompProfile':{'type':'RuntimeDefault'}},'containers':[{'name':'probe','image':'iot-coursework-feast:0.67.0','imagePullPolicy':'Never','command':['python','-c',code],
        'resources':{'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'cpu':'500m','memory':'256Mi'}},'securityContext':{'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}}}]}}
    p=out/'probe.json';save(p,probe);call('probe-apply',k+['apply','-f',str(p)])
    for _ in range(40):
        state=json.loads(subprocess.check_output(k+['get','pod',probe['metadata']['name'],'-n','kubeflow','-o','json'],text=True));phase=state.get('status',{}).get('phase')
        if phase in ['Succeeded','Failed']:break
        time.sleep(3)
    save(out/'probe-pod.json',state);call('probe-console',k+['logs',probe['metadata']['name'],'-n','kubeflow'])
    if phase!='Succeeded':raise ValueError('Native private registry probe did not succeed')
    save(out/'verification.json',{'passed':True,'completed_utc':datetime.now(timezone.utc).isoformat(),'pod_address':pod_address,'upstream_private_address':upstream,'code_sha256':digest(Path(__file__)),
        'scope':'Private CNI socket and ClusterIP only, native non-root pod health/read-only completed-run oracle. No Internet port or new cloud resource.'})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True);parser.add_argument('--data-run',required=True)
    args=parser.parse_args()
    if not re.fullmatch('[0-9a-f]{32}',args.data_run):parser.error('Use an existing completed MLflow data run id')
    run(args)
