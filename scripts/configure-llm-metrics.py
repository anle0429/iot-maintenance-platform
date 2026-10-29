"""Add narrowly scoped per-Pod LLM/MCP/worker metrics to the retained Prometheus process without removing existing jobs."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import Request,ProxyHandler,build_opener
from urllib.error import HTTPError
import argparse,base64,json,os,ssl,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from infra.llm_observer_monitoring import extended_configuration


def run(args):
    """Verify a read-only discovery identity, native configuration validity and all seven actual application targets."""
    os.umask(0o077);root=args.runtime_root.resolve();out=args.output.resolve()
    assert os.geteuid()==0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root/'outputs')
    source=json.loads((ROOT.parent/'manifest.json').read_bytes());assert source['format']=='iot-llm-monitoring-source-v1'
    for n,digest in source['files'].items():assert sha256((ROOT/n).read_bytes()).hexdigest()==digest
    out.mkdir(exist_ok=False);k=['/usr/local/bin/k3s','kubectl'];identity='iot-llm-metrics-reader';namespace='iot-observability'
    config=root/'data/runtime/monitoring-28/prometheus.json';old=config.read_bytes();existing=json.loads(old)
    runtime=root/'data/runtime/llm-monitoring-29a';runtime.mkdir(exist_ok=False);os.chown(runtime,0,65534);runtime.chmod(0o750)
    auth=runtime/'auth';auth.mkdir();os.chown(auth,0,65534);auth.chmod(0o750)
    def save(name,value):
        """Keep original public metadata and query results without writing credentials to evidence."""
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    def get(*query):
        """Read specific native administrative resources; never collect a Secret body."""
        return json.loads(subprocess.check_output(k+['get',*query,'-o','json'],stderr=subprocess.PIPE,timeout=30))
    def command(label,argv,payload=None,timeout=60):
        """Keep original bounded command exits independently; credential requests use a separate private path."""
        result=subprocess.run(argv,input=payload,capture_output=True,timeout=timeout)
        (out/(label+'.stdout')).write_bytes(result.stdout);(out/(label+'.stderr')).write_bytes(result.stderr);(out/(label+'.exit')).write_text(str(result.returncode)+'\n')
        if result.returncode:raise RuntimeError(label+' failed')
        return result.stdout
    save('source.json',source);(out/'prometheus-before.json').write_bytes(old)
    assert not subprocess.check_output(k+['get','serviceaccount',identity,'-n',namespace,'--ignore-not-found','-o','name']).strip()
    objects=[{'apiVersion':'v1','kind':'ServiceAccount','metadata':{'name':identity,'namespace':namespace},'automountServiceAccountToken':False}]
    for ns in ['iot-llm','iot-agents']:
        for kind in ['role','rolebinding']:assert not subprocess.check_output(k+['get',kind,identity,'-n',ns,'--ignore-not-found','-o','name']).strip()
        objects.extend([
            {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'Role','metadata':{'name':identity,'namespace':ns},
             'rules':[{'apiGroups':[''],'resources':['pods'],'verbs':['get','list','watch']}]},
            {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'RoleBinding','metadata':{'name':identity,'namespace':ns},
             'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':identity},
             'subjects':[{'kind':'ServiceAccount','name':identity,'namespace':namespace}]},
        ])
    save('discovery-resources.json',{'apiVersion':'v1','kind':'List','items':objects})
    command('create-discovery-identity',k+['create','-f','-'],json.dumps({'apiVersion':'v1','kind':'List','items':objects}).encode())
    # The token bytes stay only in the private auth file and request headers; no command receipt or environment dump contains them.
    issued=subprocess.run(k+['create','token',identity,'-n',namespace,'--duration=8h'],capture_output=True,check=True,timeout=30)
    token=issued.stdout.strip();assert len(token)>100
    payload=token.split(b'.')[1];claims=json.loads(base64.urlsafe_b64decode(payload+b'='*((-len(payload))%4)))
    assert claims['sub']=='system:serviceaccount:'+namespace+':'+identity and 3600<=claims['exp']-claims['iat']<=28800
    token_file=auth/'token';token_file.write_bytes(token+b'\n');os.chown(token_file,0,65534);token_file.chmod(0o640)
    ca_file=auth/'ca.crt';ca_file.write_text(get('configmap','kube-root-ca.crt','-n',namespace)['data']['ca.crt']);os.chown(ca_file,0,65534);ca_file.chmod(0o640)
    command('prometheus-can-read-auth',['runuser','-u','nobody','--','test','-r',str(token_file)])
    save('identity.json',{'subject':claims['sub'],'expires_utc':datetime.fromtimestamp(claims['exp'],timezone.utc).isoformat(),
        'lifetime_seconds':claims['exp']-claims['iat'],'token_mode':oct(token_file.stat().st_mode&0o777),'token_uid':token_file.stat().st_uid,'token_gid':token_file.stat().st_gid})
    opener=build_opener(ProxyHandler({}));context=ssl.create_default_context(cafile=str(ca_file))
    from urllib.request import HTTPSHandler
    authenticated=build_opener(ProxyHandler({}),HTTPSHandler(context=context))
    for ns in ['iot-llm','iot-agents']:
        for resource,status in [('pods',200),('secrets',403)]:
            req=Request('https://10.53.0.1:443/api/v1/namespaces/'+ns+'/'+resource,headers={'Authorization':'Bearer '+token.decode()})
            try:response=authenticated.open(req,timeout=15)
            except HTTPError as error:response=error
            with response:body=response.read(2*1024**2+1)
            assert response.status==status and len(body)<=2*1024**2
            (out/('permission-'+ns+'-'+resource+'.json')).write_bytes(body)
            save('permission-'+ns+'-'+resource+'-status.json',{'status':response.status})
    planned=extended_configuration(existing,token_file,ca_file);save('prometheus-planned.json',planned)
    binary=root/'data/runtime/monitoring-assets-28/prometheus/prometheus-3.13.5.linux-amd64/promtool'
    command('configuration-check',[str(binary),'check','config',str(out/'prometheus-planned.json')])
    prior=json.loads(command('prometheus-pid-before',['systemctl','show','iot-prometheus-28','-p','MainPID','--value']).decode().strip())
    assert prior>0 and config.read_bytes()==old
    temporary=config.with_name('prometheus-llm-29a.pending');temporary.write_bytes((out/'prometheus-planned.json').read_bytes());temporary.chmod(0o644);os.replace(temporary,config)
    command('reload',['systemctl','kill','--kill-who=main','--signal=HUP','iot-prometheus-28'])
    wanted={'iot-agent-mcp':4,'iot-llm-observer':1,'iot-llm-model':1,'iot-llm-gpu-host':1};observations=[]
    for attempt in range(30):
        with opener.open('http://127.0.0.1:19091/api/v1/targets',timeout=10) as response:raw=response.read(4*1024**2+1);assert response.status==200 and len(raw)<=4*1024**2
        (out/('targets-'+str(attempt)+'.json')).write_bytes(raw);data=json.loads(raw)
        selected=[v for v in data['data']['activeTargets'] if v['labels'].get('job') in wanted]
        counts={job:sum(t['labels']['job']==job for t in selected) for job in wanted}
        observations.append({'attempt':attempt,'counts':counts,'up':sum(t['health']=='up' for t in selected)})
        if counts==wanted and all(t['health']=='up' for t in selected):break
        time.sleep(2)
    else:raise ValueError('Preserve original target observations; the new seven-target monitoring cohort is not healthy')
    current=int(command('prometheus-pid-after',['systemctl','show','iot-prometheus-28','-p','MainPID','--value']).decode().strip());assert current==prior
    assert json.loads(config.read_bytes())==planned and planned['scrape_configs'][:-4]==existing['scrape_configs']
    save('observations.json',observations)
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':source['commit'],'prometheus_main_pid':current,
        'original_jobs_preserved':True,'new_target_counts':counts,'new_targets_up':len(selected),'secret_access_denied_both_namespaces':True,
        'scope':'Seven real per-Pod LLM/MCP/host targets discovered through a privately readable, read-only scoped identity. Existing jobs and failures remain visible. Numeric dashboard, log and UI acceptance remain separate.'}
    save('verification.json',report)
    variants=[token,base64.b64encode(token)]
    for p in out.rglob('*'):
        if p.is_file():assert not any(v in p.read_bytes() for v in variants),'Private discovery token refused from evidence'
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
