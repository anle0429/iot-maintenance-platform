"""Upgrade the existing one-GPU release to two pinned local models, preserving the primary alias and restoring it on failure."""
from copy import deepcopy
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError
import argparse,base64,json,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from infra.llm_ab_models import monitoring_configuration,MODELS


def run(args):
    """Validate chart/monitoring first, warm both real artifacts, then verify routed primary traffic and actual model-specific targets."""
    os.umask(0o077);root=args.runtime_root.resolve();out=args.output.resolve();assert os.geteuid()==0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root/'outputs')
    source=json.loads((ROOT.parent/'manifest.json').read_bytes());assert source['format']=='iot-llm-ab-models-source-v1'
    for name,pin in source['files'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==pin,name
    session=json.loads((root/'data/runtime/ci/session.json').read_bytes());assert (datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc)).total_seconds()>1800
    prepared=json.loads(args.preparation.read_bytes());small=json.loads((ROOT/'infra/llm/model.lock.json').read_bytes())
    assert prepared['passed'] and prepared['model_sha256']==small['sha256'] and prepared['model_bytes']==small['bytes'] and not prepared['running_services_changed']
    assert datetime.fromisoformat(prepared['deadline_utc'])-datetime.now(timezone.utc)>__import__('datetime').timedelta(minutes=30)
    out.mkdir(exist_ok=False);k=['/usr/local/bin/k3s','kubectl'];helm=[str(root/'data/runtime/kubernetes-assets-27/helm/helm'),'--kubeconfig','/etc/rancher/k3s/k3s.yaml'];opener=build_opener(ProxyHandler({}))
    def save(name,value):
        """Keep public source, deployment and native response receipts without collecting Secret objects."""
        (out/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    def command(name,argv,timeout=90,payload=None,check=True):
        """Preserve original command exit and streams, including a failed rollout or rollback."""
        p=subprocess.run(argv,input=payload,capture_output=True,timeout=timeout)
        (out/(name+'.stdout')).write_bytes(p.stdout);(out/(name+'.stderr')).write_bytes(p.stderr);save(name+'-exit.json',{'exit_code':p.returncode})
        if check and p.returncode:raise RuntimeError(name+' failed')
        return p
    def get(name,*query):
        """Read named non-secret native identities for guarded changes and final comparisons."""
        return json.loads(command(name,k+['get',*query,'-o','json']).stdout)
    def http(name,path,body=None):
        """Save bounded original responses from the private model/router endpoints."""
        payload=json.dumps(body).encode() if body is not None else None
        request=Request(base+path,data=payload,headers={'Content-Type':'application/json'})
        try:response=opener.open(request,timeout=240)
        except HTTPError as error:response=error
        with response:raw=response.read(2000000);status=response.status
        assert len(raw)<2000000;(out/(name+'.body')).write_bytes(raw);save(name+'.json',{'status':status,'bytes':len(raw),'sha256':sha256(raw).hexdigest()})
        assert status==200,name
        return json.loads(raw) if not path.startswith('/metrics') else raw
    save('source.json',source);save('preparation.json',prepared)
    before=get('model-before','deployment','iot-model-gpu','-n','iot-llm');service=get('service-before','service','iot-model-gpu','-n','iot-llm')
    original_other={}
    for name in ['iot-llm-observer','iot-model-control']:
        original_other[name]=get(name+'-before','deployment',name,'-n','iot-llm')
    assert service['spec']['type']=='ClusterIP' and not service['spec'].get('externalIPs')
    spec=before['spec']['template']['spec'];assert spec['nodeSelector']['kubernetes.io/hostname']=='iot-cw-gpu' and len(spec['containers'])==1
    assert spec['containers'][0]['image']=='ghcr.io/ggml-org/llama.cpp@sha256:d854233e9f043046f363953047ff518a9792b044cea1d53f86d299e88344cd5f'
    assert '--model' in spec['containers'][0]['args'] and '--models-preset' not in spec['containers'][0]['args']
    base='http://'+service['spec']['clusterIP']+':8080'
    history=json.loads(command('history-before',helm+['history','iot-model-gpu','-n','iot-llm','-o','json']).stdout);revision=history[-1]['revision'];assert history[-1]['status']=='deployed'
    command('gpu-before',k+['exec','deployment/iot-model-gpu','-n','iot-llm','--','nvidia-smi','--query-gpu=name,memory.total,memory.used,utilization.gpu','--format=csv,noheader'])
    chart=ROOT/'infra/charts/iot-gpu-multimodel';command('lint',helm+['lint',str(chart)])
    rendered=command('render',helm+['template','iot-model-gpu',str(chart),'-n','iot-llm']).stdout
    command('server-validation',k+['apply','--dry-run=server','-n','iot-llm','-f','-'],payload=rendered)
    config=root/'data/runtime/monitoring-28/prometheus.json';old=config.read_bytes();planned=monitoring_configuration(json.loads(old))
    transition=deepcopy(planned);transition['scrape_configs']=transition['scrape_configs'][:-2]
    (out/'prometheus-before.json').write_bytes(old);save('prometheus-transition.json',transition);save('prometheus-planned.json',planned)
    promtool=root/'data/runtime/monitoring-assets-28/prometheus/prometheus-3.13.5.linux-amd64/promtool'
    for name in ['transition','planned']:command('promtool-'+name,[str(promtool),'check','config',str(out/('prometheus-'+name+'.json'))])
    pid=int(command('prometheus-pid-before',['systemctl','show','iot-prometheus-28','-p','MainPID','--value']).stdout);assert pid>0
    owned=old;rollout=False;rollback=[]
    def reload(name,raw):
        """Replace only the authenticated current config and retain the existing Prometheus process/data."""
        nonlocal owned
        assert config.read_bytes()==owned,'An external config change must not be overwritten'
        pending=config.with_name('llm-ab.pending');pending.write_bytes(raw);pending.chmod(0o644);os.replace(pending,config);owned=raw
        command(name,['systemctl','kill','--kill-who=main','--signal=HUP','iot-prometheus-28'])
    try:
        assert get('model-concurrency-check','deployment','iot-model-gpu','-n','iot-llm')['spec']==before['spec']
        reload('monitoring-transition',(out/'prometheus-transition.json').read_bytes())
        began=time.monotonic();rollout=True
        command('upgrade',helm+['upgrade','iot-model-gpu',str(chart),'-n','iot-llm','--atomic','--wait','--timeout=10m'],timeout=660)
        for model in MODELS:http('load-'+model,'/models/load',{'model':model})
        models=http('loaded-models','/models');save('loaded-models-decoded.json',models)
        for model in MODELS:
            result=http('smoke-'+model,'/v1/chat/completions',{'model':model,'messages':[{'role':'user','content':'Name one common industrial pump sensor.'}],
                'max_tokens':16,'temperature':0,'seed':7,'cache_prompt':False})
            assert result['model']==model and result['usage']['completion_tokens']>0 and result['choices'][0]['message']['content']
            http('metrics-'+model,'/metrics?model='+model)
        primary_elapsed=time.monotonic()-began
        router=get('router-service','service','iot-router-epp','-n','iot-llm')
        private=json.loads(subprocess.check_output(k+['get','secret','iot-llm-observer-client','-n','iot-llm','-o','json'],timeout=30,stderr=subprocess.PIPE))
        token=base64.b64decode(private['data']['API_KEY']).decode();private=None
        body={'model':MODELS[0],'messages':[{'role':'user','content':'Name one common industrial pump sensor.'}],'max_tokens':16,'temperature':0,'seed':7,'cache_prompt':False}
        request=Request('http://'+router['spec']['clusterIP']+':8081/v1/chat/completions',data=json.dumps(body).encode(),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
        with opener.open(request,timeout=180) as response:active=response.read(1048576);assert response.status==200
        (out/'active-primary-response.json').write_bytes(active);accepted=json.loads(active)
        assert accepted['model']==MODELS[0] and accepted['usage']['completion_tokens']>0 and accepted['choices'][0]['message']['content']
        reload('monitoring-final',(out/'prometheus-planned.json').read_bytes())
        for attempt in range(20):
            with opener.open('http://127.0.0.1:19091/api/v1/targets',timeout=15) as response:raw=response.read()
            (out/('targets-'+str(attempt)+'.json')).write_bytes(raw);targets=json.loads(raw)['data']['activeTargets']
            selected=[v for v in targets if v['labels'].get('job') in {'iot-llm-model','iot-llm-model-small','iot-llm-ab'}]
            if len(selected)==3 and all(v['health']=='up' for v in selected):break
            time.sleep(2)
        else:raise ValueError('Keep the original target diagnostics: model-specific scrapes did not become healthy')
        current=get('model-after','deployment','iot-model-gpu','-n','iot-llm');current_service=get('service-after','service','iot-model-gpu','-n','iot-llm')
        assert current['metadata']['uid']==before['metadata']['uid'] and current_service['spec']==service['spec']
        for name,prior in original_other.items():
            after=get(name+'-after','deployment',name,'-n','iot-llm');assert after['metadata']['uid']==prior['metadata']['uid'] and after['spec']==prior['spec']
        assert int(command('prometheus-pid-after',['systemctl','show','iot-prometheus-28','-p','MainPID','--value']).stdout)==pid
        command('model-logs',k+['logs','deployment/iot-model-gpu','-n','iot-llm','--tail=250'])
        command('gpu-after',k+['exec','deployment/iot-model-gpu','-n','iot-llm','--','nvidia-smi','--query-gpu=name,memory.total,memory.used,utilization.gpu','--format=csv,noheader'])
        report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':source['commit'],'models':list(MODELS),
            'deployment_uid_preserved':True,'service_spec_preserved':True,'observer_and_adapter_specs_preserved':True,
            'upgrade_to_both_warm_seconds':primary_elapsed,'authenticated_primary_generation_status':200,'model_specific_targets_up':3,'prometheus_main_pid':pid,
            'scope':'Two pinned artifact sizes in one existing one-GPU server-router container, explicit local presets, native model-specific replies/metrics and preserved primary route. Recreate has downtime; this is not an A/B outcome or zero-downtime claim.'}
        save('verification.json',report);print(json.dumps(report))
    except Exception as error:
        if rollout:
            latest=get('model-on-error','deployment','iot-model-gpu','-n','iot-llm')
            if latest['spec']!=before['spec']:
                assert latest['metadata']['uid']==before['metadata']['uid']
                volumes=latest['spec']['template']['spec'].get('volumes',[])
                assert any(v.get('configMap',{}).get('name')=='iot-gpu-model-presets-30a' for v in volumes),'Preserve an unrelated replacement'
                p=command('rollback',helm+['rollback','iot-model-gpu',str(revision),'-n','iot-llm','--wait','--timeout=8m'],timeout=510,check=False)
                rollback.append({'resource':'model-release','exit_code':p.returncode})
        if config.read_bytes()==owned:
            reload('monitoring-restore',old);rollback.append({'resource':'prometheus-config','restored':config.read_bytes()==old})
        save('verification.json',{'passed':False,'checked_utc':datetime.now(timezone.utc).isoformat(),'error_type':type(error).__name__,'error':str(error),'rollback':rollback})
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output','preparation']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
