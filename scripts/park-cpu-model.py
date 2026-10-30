"""Release the unused CPU-model reservation after verifying that the selected agent route still uses the loaded GPU models."""
from pathlib import Path
from datetime import datetime,timezone
from hashlib import sha256
from urllib.request import ProxyHandler,build_opener
import argparse,json,os,subprocess,time
ROOT=Path(__file__).resolve().parents[1]


def run(args):
    """Scale only the owned idle legacy server to zero; preserve its Deployment, Service, files and one-command restoration path."""
    root=args.runtime_root.resolve();out=args.output.resolve();assert os.geteuid()==0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root/'outputs')
    seal=json.loads((ROOT.parent/'manifest.json').read_bytes());assert seal['format']=='iot-cpu-model-standby-source-v1'
    for name,pin in seal['files'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==pin
    session=json.loads((root/'data/runtime/ci/session.json').read_bytes());assert (datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc)).total_seconds()>300
    os.umask(0o077);out.mkdir(exist_ok=False);k=['/usr/local/bin/k3s','kubectl'];opener=build_opener(ProxyHandler({}))
    def save(name,value):
        """Retain public resource identities and measurements without any Secret read."""
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    def get(label,*query):
        """Capture actual non-secret cluster objects before changing the single owned replica count."""
        raw=subprocess.check_output(k+['get',*query,'-o','json'],timeout=30,stderr=subprocess.PIPE);(out/(label+'.json')).write_bytes(raw);return json.loads(raw)
    accepted=root/'outputs/llm-ab-models-deploy-30b';assert json.loads((accepted/'verification.json').read_bytes())['passed']
    control=get('control','deployment','iot-model-control','-n','iot-llm')
    assert control['spec']==json.loads((accepted/'iot-model-control-before.stdout').read_bytes())['spec']
    observer=get('observer','deployment','iot-llm-observer','-n','iot-llm')
    assert observer['spec']==json.loads((accepted/'iot-llm-observer-before.stdout').read_bytes())['spec']
    shared=get('shared-model','modelconfig','default-model-config','-n','iot-agents')
    assert shared['spec']['model']=='iot-qwen2.5-7b' and shared['spec']['openAI']['baseUrl']=='http://iot-router-epp.iot-llm.svc.cluster.local:8081/v1'
    gpu=get('gpu-service','service','iot-model-gpu','-n','iot-llm')
    with opener.open('http://'+gpu['spec']['clusterIP']+':8080/models',timeout=15) as response:models=json.load(response)
    assert {v['id'] for v in models['data'] if v['status']['value']=='loaded'}=={'iot-qwen2.5-1.5b','iot-qwen2.5-7b'};save('loaded-models.json',models)
    cpu=get('cpu-before','deployment','iot-model','-n','iot-llm');service=get('cpu-service-before','service','iot-model','-n','iot-llm')
    assert cpu['spec']['replicas']==1 and cpu['spec']['template']['spec']['containers'][0]['image']==json.loads((ROOT/'infra/llm/model.lock.json').read_bytes())['server_image']
    with opener.open('http://'+service['spec']['clusterIP']+':8080/slots',timeout=15) as response:slots=json.load(response)
    assert isinstance(slots,list) and slots and all(v.get('is_processing') is False for v in slots);save('cpu-slots-before.json',slots)
    patch=[{'op':'test','path':'/metadata/uid','value':cpu['metadata']['uid']},{'op':'test','path':'/metadata/resourceVersion','value':cpu['metadata']['resourceVersion']},{'op':'test','path':'/spec/replicas','value':1},{'op':'replace','path':'/spec/replicas','value':0}]
    result=subprocess.run(k+['patch','deployment','iot-model','-n','iot-llm','--type=json','-p',json.dumps(patch),'-o','json'],capture_output=True,timeout=30)
    (out/'park.stdout').write_bytes(result.stdout);(out/'park.stderr').write_bytes(result.stderr);assert result.returncode==0
    after=get('cpu-after','deployment','iot-model','-n','iot-llm');expected=json.loads(json.dumps(cpu['spec']));expected['replicas']=0
    assert after['metadata']['uid']==cpu['metadata']['uid'] and after['spec']==expected
    assert get('cpu-service-after','service','iot-model','-n','iot-llm')['spec']==service['spec']
    assert get('control-after','deployment','iot-model-control','-n','iot-llm')['spec']==control['spec']
    assert get('observer-after','deployment','iot-llm-observer','-n','iot-llm')['spec']==observer['spec']
    save('source.json',seal);report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':seal['commit'],
        'legacy_cpu_replicas_before':1,'legacy_cpu_replicas_after':0,'gpu_models_loaded':2,'selected_agent_model':'iot-qwen2.5-7b',
        'cpu_container_requests_preserved':cpu['spec']['template']['spec']['containers'][0]['resources']['requests'],
        'scope':'Only the idle legacy CPU-model replica was parked. Its files, Deployment UID and Service are retained. The accepted GPU adapter/observer specs and shared model selection are unchanged. Restore with kubectl scale deployment/iot-model -n iot-llm --replicas=1 if the CPU prototype is needed.'}
    save('verification.json',report);print(json.dumps(report))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
