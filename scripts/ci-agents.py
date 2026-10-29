"""Test, package, deploy and verify three separate private MCP/agent releases on the retained coursework lab."""
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from pathlib import Path
from urllib.request import ProxyHandler,Request,build_opener
import importlib.util,json,os,shutil,subprocess,sys,tarfile,time,xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agents.ci_contract import release_identity,require_receipt,validate_source,validate_agents
from agents.release_ci import MCP_RUNTIME,MCP_TESTS,api_quality,feature_lease,packaged_paths,ready_agents,role_for_job,unchanged_quality_source
from agents.answer_acceptance import verify_specialist
from agents.delegation_acceptance import verify_coordinator
from agents.probe import run as probe
from ml.serving_ci import host_source_path,docker_config_digest,check_pods

SPEC=importlib.util.spec_from_file_location('agent_ci_shared',ROOT/'scripts/ci-serving.py')
SHARED=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(SHARED)


def file_hash(path):
    """Hash an image export incrementally so the CI controller never loads its layers into RAM."""
    digest=sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


class Context(SHARED.Context):
    """Reuse bounded native control transport while isolating each role's source, build and receipts."""
    driver='/src/scripts/ci-agents.py'

    def __init__(self):
        """Accept only an explicitly declared Jenkins job and its full trusted source revision."""
        if os.environ.get('IOT_CI_PROVIDER')!='jenkins' or not os.environ.get('JENKINS_URL'):
            raise ValueError('Run this procedure in its existing private Jenkins controller')
        self.role=role_for_job(os.environ.get('JOB_BASE_NAME'));self.commit=os.environ['IOT_CI_COMMIT'];self.version='6'
        self.identity=release_identity(self.role,os.environ['BUILD_NUMBER'],self.commit);self.release=self.identity['release']
        self.deploy=Path(os.environ['IOT_DEPLOY_ROOT']).resolve();self.directory=self.deploy/'data/ci/releases'/self.release
        self.out=self.directory/'outputs';self.host_root=host_source_path(ROOT,self.deploy)
        self.seal=json.loads((self.deploy/'data/ci/source'/self.commit/'manifest.json').read_bytes());self.source_check()
        self.k=['/usr/local/bin/k3s','kubectl'];self.helm=str(self.deploy/'data/runtime/kubernetes-assets-27/helm/helm')
        self.description='CI:'+self.release+':'+self.commit

    def source_check(self):
        """Refuse changed source in every outer and native phase."""
        self.source_digest=validate_source(ROOT,self.seal,self.commit)

    def phase(self,name,details):
        """Bind a native stage to this exact job/build/source."""
        SHARED.save(self.out/(name+'.json'),{'schema_version':1,'phase':name,'passed':True,**self.identity,**details})

    def prior(self,name):
        """Prevent copied success receipts from advancing another component or build."""
        return require_receipt(json.loads((self.out/(name+'.json')).read_bytes()),self.identity,name)

    def native(self,phase,cluster=False,docker=False):
        """Mount only trusted read-only source, this release's artifacts and the explicit private control interfaces."""
        extra=['--env','JOB_BASE_NAME','--mount','type=bind,src=/srv/iot-fast/ci,dst=/srv/iot-fast/ci,readonly']
        if docker:
            for path in ['/usr/bin/docker','/var/run/docker.sock','/run/k3s/containerd/containerd.sock']:
                extra+=['--mount','type=bind,src='+path+',dst='+path+(',readonly' if path=='/usr/bin/docker' else '')]
        self.control(phase,cluster=cluster,extra=extra,memory='2g')

    def history(self,release):
        """Read the actual latest Helm revision before deciding whether this CI job owns a rollback."""
        return json.loads(subprocess.check_output([self.helm,'history',release,'-n','iot-agents','--max','1','-o','json']))[0]


def image_files(image,paths):
    """Read the tested package from its actual non-root image rather than trusting a Docker build exit code."""
    names={name:('/tests/'+name.removeprefix('tests/') if name.startswith('tests/') else '/workspace/'+name) for name in paths}
    code='import hashlib,json,os,pathlib;assert os.getuid()==10001; print(json.dumps({n:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for n,p in '+repr(names)+'.items()}))'
    return json.loads(subprocess.check_output(['docker','run','--rm','--network','none','--read-only','--memory','256m','--cpus','.5',
                                               '--entrypoint','python',image,'-B','-c',code],timeout=60))


def build_overlay(c,tests=False):
    """Extend the exact retained SDK image without dependency downloads or API source edits."""
    expected=MCP_TESTS if tests else MCP_RUNTIME;parent_tag='iot-coursework-mcp'+('-tests' if tests else '')+':mcp-2cf8e6898c93'
    parent=json.loads(subprocess.check_output(['docker','image','inspect',parent_tag]))[0]
    if parent['Id']!=expected:raise ValueError('The retained MCP dependency image changed')
    pinned='iot-ci-mcp-parent:'+expected.split(':')[1];subprocess.run(['docker','tag',expected,pinned],check=True)
    tag='iot-ci-mcp'+('-tests' if tests else '')+':'+c.release
    filename='AgentsTests.Dockerfile' if tests else 'AgentsApplication.Dockerfile'
    c.log('build-api-tests' if tests else 'build-application',['docker','build','--pull=false','--build-arg','RUNTIME='+pinned,
          '--label','org.opencontainers.image.revision='+c.commit,'-f',str(ROOT/'infra/ci'/filename),'-t',tag,str(ROOT)],600)
    actual=json.loads(subprocess.check_output(['docker','image','inspect',tag]))[0]
    if (actual['RootFS']['Layers'][:len(parent['RootFS']['Layers'])]!=parent['RootFS']['Layers']
            or actual['Config']['Labels']['org.opencontainers.image.revision']!=c.commit):
        raise ValueError('The MCP overlay does not extend its verified dependency layers')
    packaged=image_files(actual['Id'],packaged_paths(c.seal,tests))
    if packaged!={name:c.seal['files'][name] for name in packaged}:raise ValueError('The built MCP package differs from tested source')
    return {'image':tag,'image_id':actual['Id'],'parent_image_id':parent['Id'],'packaged_sha256':packaged,'revision_label':c.commit}


def get_http(url,headers=None):
    """Read a private native JSON response without proxies or credential-bearing diagnostics."""
    with build_opener(ProxyHandler({})).open(Request(url,headers=headers or {}),timeout=30) as response:return json.load(response)


def live_state(c,label):
    """Observe accepted agents, quiet pools, global model configuration and the genuine feature lease before release work."""
    agents=c.get('get','sandboxagents','-n','iot-agents');policy=json.loads((ROOT/'agents/response-policy.json').read_bytes())
    ready_agents(agents['items'],policy)
    pools=c.get('get','workerpools','-n','iot-agents')
    expected={'iot-'+role+'-pool' for role in ['retrieval','drift','coordinator']}
    if {p['metadata']['name'] for p in pools['items']}!=expected or any(p['spec']['replicas']!=2 or not p['spec'].get('selector') for p in pools['items']):
        raise ValueError('Wait for all three retained worker pools to return to their two-worker idle floor')
    model=c.get('get','modelconfig','default-model-config','-n','iot-agents');spec=model['spec']
    if (spec['provider']!='OpenAI' or spec['model']!='iot-qwen2.5-7b' or spec['apiKeySecret']!='iot-local-model-client'
            or spec['openAI']['baseUrl']!='http://iot-router-epp.iot-llm.svc.cluster.local:8081/v1'
            or spec['openAI']['maxTokens']!=768 or spec['openAI']['timeout']!=600):raise ValueError('Preserve the accepted shared private GPU model configuration')
    base=c.service_url('kagent-controller','iot-agents')+':8083';actors=get_http(base+'/api/substrate/status')
    if any(a['atespace']=='iot-agents' and a['status'] in ['Running','Resuming','Suspending'] for a in actors['data']['actors']):
        raise ValueError('Wait for active agent work to finish before a shared release')
    feature=c.get('get','deployment','iot-feature-api','-n','iot-features')
    seed=next(v['configMap']['name'] for v in feature['spec']['template']['spec']['volumes'] if v['name']=='seed')
    mapping=c.get('get','configmap',seed,'-n','iot-features');context=json.loads(mapping['data']['context.json'])
    lease=feature_lease(context,datetime.now(timezone.utc))
    gpu_path=c.deploy/'data/runtime/ci/gpu-session.json';gpu=json.loads(gpu_path.read_bytes())
    stop=datetime.fromisoformat(gpu['last_start_utc'])+timedelta(seconds=gpu['max_duration_seconds'])
    if (gpu['max_duration_seconds']!=28800 or gpu['termination_action']!='STOP' or gpu['status']!='RUNNING'
            or gpu['private_ip']!='10.42.0.7' or (stop-datetime.now(timezone.utc)).total_seconds()<900):
        raise ValueError('Require fifteen actual GPU session minutes; never rewrite its deadline')
    state={'agents':agents,'pools':pools,'model_config':model,'actors':actors,'feature_lease':lease,
           'feature_seed_uid':mapping['metadata']['uid'],'gpu_session':gpu,'gpu_session_sha256':file_hash(gpu_path),'gpu_stop_utc':stop.isoformat()}
    SHARED.save(c.out/(label+'.json'),state);return state


def package_charts(c):
    """Package both reviewed charts and record source plus actual package hashes before any upgrade."""
    import yaml
    destination=c.out/'charts';destination.mkdir();charts={}
    for name in ['iot-agents']+([] if c.role=='coordinator' else ['iot-mcp']):
        source=ROOT/'infra/charts'/name;metadata=yaml.safe_load((source/'Chart.yaml').read_bytes())
        command=[c.helm,'package',str(source),'--destination',str(destination)]
        if name=='iot-agents':command+=['--app-version','0.2.2']
        c.log('package-'+name,command)
        path=destination/(name+'-'+metadata['version']+'.tgz');packaged={}
        with tarfile.open(path) as archive:
            for member in archive:
                if not member.isfile():continue
                relative=Path(member.name).relative_to(name).as_posix();raw=archive.extractfile(member).read()
                expected=(source/relative).read_bytes()
                if relative=='Chart.yaml':
                    declared=yaml.safe_load(expected)
                    if name=='iot-agents':declared['appVersion']='0.2.2'
                    if yaml.safe_load(raw)!=declared:raise ValueError('Packaged chart metadata changed unexpectedly')
                elif raw!=expected:raise ValueError('Packaged chart differs from tested source: '+relative)
                packaged[relative]=sha256(raw).hexdigest()
        expected_names={p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file()}
        if set(packaged)!=expected_names:raise ValueError('A packaged chart input was omitted or added')
        charts[name]={'path':path.relative_to(c.out).as_posix(),'sha256':file_hash(path),'packaged_files':packaged}
    application=json.loads((c.out/'application.json').read_bytes()) if c.role!='coordinator' else None
    c.phase('build',{'source_digest':c.source_digest,'charts':charts,'application':application})


def chart_path(c,build,name):
    """Authenticate the exact tested package again before deployment."""
    value=build['charts'][name];path=c.out/value['path']
    if not path.is_relative_to(c.out) or file_hash(path)!=value['sha256']:raise ValueError('The tested chart package changed')
    return str(path)


def deploy_host(c):
    """Upgrade only the owned MCP release and reapply the accepted shared agent chart atomically."""
    import yaml
    build=c.prior('build');before=live_state(c,'before-deploy');plan={'before':{},'description':c.description}
    for release in ['iot-agents']+([] if c.role=='coordinator' else ['iot-'+c.role+'-mcp']):
        history=c.history(release)
        if history['status']!='deployed':raise ValueError('Preserve an unfinished Helm release: '+release)
        values=json.loads(subprocess.check_output([c.helm,'get','values',release,'-n','iot-agents','--all','-o','json']))
        plan['before'][release]={'revision':history['revision'],'values':values}
    SHARED.save(c.out/'deployment-plan.json',plan)
    if c.role!='coordinator':
        app=build['application'];image=c.directory/'mcp-image.tar'
        if file_hash(image)!=app['export_sha256']:raise ValueError('The tested image export changed')
        c.log('import-image',['/usr/local/bin/k3s','ctr','images','import',str(image)],600)
        name='iot-'+c.role+'-mcp';values=dict(plan['before'][name]['values'])
        values.update(image={'repository':app['image'].split(':')[0],'tag':app['image'].split(':')[1]},sourceSha256=c.source_digest,revisionMarker=c.release)
        SHARED.save(c.out/'mcp-values.json',values)
        c.log('upgrade-mcp',[c.helm,'upgrade',name,chart_path(c,build,'iot-mcp'),'-n','iot-agents','-f',str(c.out/'mcp-values.json'),
              '--description',c.description,'--atomic','--wait','--timeout','300s'],420)
        pods=c.get('get','pods','-n','iot-agents','-l','app.kubernetes.io/instance='+name)
        uids=check_pods(pods['items'],app['image'],app['imported_config_digest'],'mcp')
        SHARED.save(c.out/'mcp-pods-after.json',pods);SHARED.save(c.out/'mcp-pod-uids.json',uids)
    agent_values=plan['before']['iot-agents']['values'];SHARED.save(c.out/'agent-values.json',agent_values)
    renderer=str(ROOT/'scripts/render-agent-responses.py');chart=chart_path(c,build,'iot-agents')
    rendered=subprocess.check_output([c.helm,'template','iot-agents',chart,'-n','iot-agents','-f',str(c.out/'agent-values.json'),'--post-renderer',renderer])
    documents=list(yaml.safe_load_all(rendered));validate_agents(documents,json.loads((ROOT/'agents/response-policy.json').read_bytes()))
    (c.out/'rendered-agents.yaml').write_bytes(rendered)
    c.log('agent-server-dry-run',c.k+['apply','--dry-run=server','-f',str(c.out/'rendered-agents.yaml'),'-o','json'])
    c.log('upgrade-agents',[c.helm,'upgrade','iot-agents',chart,'-n','iot-agents','-f',str(c.out/'agent-values.json'),
          '--post-renderer',renderer,'--description',c.description,'--atomic','--wait','--timeout','240s'],330)
    deadline=time.monotonic()+180
    while True:
        after=c.get('get','sandboxagents','-n','iot-agents')
        try:ready_agents(after['items'],json.loads((ROOT/'agents/response-policy.json').read_bytes()));break
        except ValueError:
            if time.monotonic()>=deadline:raise
            time.sleep(3)
    pools=c.get('get','workerpools','-n','iot-agents');original={p['metadata']['name']:p['spec'] for p in before['pools']['items']}
    if {p['metadata']['name']:p['spec'] for p in pools['items']}!=original:raise ValueError('The shared reapply changed accepted worker/KEDA configuration')
    model=c.get('get','modelconfig','default-model-config','-n','iot-agents')
    if model['spec']!=before['model_config']['spec']:raise ValueError('The shared model configuration changed')
    SHARED.save(c.out/'agents-after.json',after);SHARED.save(c.out/'pools-after.json',pools)
    c.phase('deploy',{'source_digest':c.source_digest,'role':c.role,'current_policy':'0.2.2',
                      'helm_revisions':{name:c.history(name) for name in plan['before']},
                      'shared_agent_reapply':'Same accepted three-role policy and pools; no agent-version change claimed.',
                      'new_mcp_image':build['application']['image_id'] if build['application'] else None})


def verify_host(c):
    """Require a native MCP probe and an actual complete role answer with independently verified tool or child histories."""
    c.prior('deploy');base=c.service_url('kagent-controller','iot-agents')+':8083'
    if c.role!='coordinator':
        c.log('native-mcp-probe',c.k+['exec','-n','iot-agents','deployment/iot-'+c.role+'-mcp','-c','mcp','--',
              'python','-B','-m','services.mcp_tools.probe','--role',c.role,'--run',c.release],240)
        if not json.loads((c.out/'native-mcp-probe.txt').read_bytes())['passed']:raise ValueError('Native MCP business probe failed')
    requests=c.out/'agent-request';probe(base,requests,[c.role],specialist_timeout=300,coordinator_timeout=600)
    reply=json.loads((requests/(c.role+'-response.json')).read_bytes())
    if c.role=='coordinator':
        identity=json.loads((requests/'identity.json').read_bytes())['sub'];children={}
        for message in reply['result']['history']:
            for part in message.get('parts',[]):
                if part.get('metadata',{}).get('adk_type')!='function_response':continue
                data=part['data'];role={'iot_agents__NS__iot_retrieval':'retrieval','iot_agents__NS__iot_drift':'drift'}.get(data['name'])
                if not role:continue
                session=data['response']['subagent_session_id'];children[role]={}
                for kind,suffix in [('session',''),('tasks','/tasks')]:
                    raw=get_http(base+'/api/sessions/'+session+suffix,{'X-User-Id':identity})
                    SHARED.save(requests/(role+'-'+kind+'.json'),raw);children[role][kind]=raw
        accepted=verify_coordinator(reply,children,knowledge_root=ROOT/'knowledge')
    else:accepted=verify_specialist(reply,c.role,knowledge_root=ROOT/'knowledge')
    SHARED.save(c.out/'agent-acceptance.json',accepted)
    c.phase('verify',{'actual_agent_acceptance':accepted,'native_mcp_probe':c.role!='coordinator',
                      'source_digest':c.source_digest,'coursework_complete':False})


def rollback_host(c):
    """Restore only releases whose latest Helm description still belongs to this failed CI build."""
    plan=json.loads((c.out/'deployment-plan.json').read_bytes());actions={}
    for name in ['iot-agents']+([] if c.role=='coordinator' else ['iot-'+c.role+'-mcp']):
        current=c.history(name);before=plan['before'][name]
        if current.get('description')==c.description and current['revision']!=before['revision']:
            c.log('rollback-'+name,[c.helm,'rollback',name,str(before['revision']),'-n','iot-agents','--wait','--timeout','240s'],330)
            values=json.loads(subprocess.check_output([c.helm,'get','values',name,'-n','iot-agents','--all','-o','json']))
            if values!=before['values']:raise ValueError('Automatic rollback did not restore the original values')
            actions[name]={'restored_revision':before['revision']}
        else:actions[name]={'no_ci_owned_latest_revision':True}
    SHARED.save(c.out/'rollback.json',{'passed':True,**c.identity,'actions':actions})


def run(phase):
    """Execute exactly one Jenkins/controller phase and preserve all failed attempts for inspection."""
    c=Context()
    if phase=='admission':
        budget=SHARED.real_admission(c,5400);c.directory.mkdir(exist_ok=False);c.out.mkdir();c.out.chmod(0o1777)
        SHARED.save(c.out/'admission.json',{'identity':c.identity,'source_digest':c.source_digest,'budget':budget});return
    if phase not in ['archive','rollback']:
        with (c.out/(phase+'.attempt')).open('x') as stream:stream.write(datetime.now(timezone.utc).isoformat())
        SHARED.real_admission(c,300 if phase=='rollback-host' else 900)
    if phase=='test':
        c.log('controller-build',['docker','build','--label','org.opencontainers.image.revision='+c.commit,
              '-f',str(ROOT/'infra/ci/ServingController.Dockerfile'),'-t','iot-ci-agent-controller:'+c.release,str(ROOT)])
        image=json.loads(subprocess.check_output(['docker','image','inspect','iot-ci-agent-controller:'+c.release]))[0]
        SHARED.save(c.out/'controller.json',{'id':image['Id']});c.native('test-host')
        if c.role!='coordinator':
            tested=build_overlay(c,True);SHARED.save(c.out/'test-image.json',tested)
            proof=c.out/'api-tests';proof.mkdir();proof.chmod(0o1777)
            command=['docker','run','--name',c.release+'-api-tests','--network','none','--read-only','--memory','2g','--memory-swap','2g','--cpus','1',
                '--tmpfs','/tmp:rw,nosuid,size=512m','--mount','type=bind,src='+str(proof)+',dst=/proof',tested['image_id'],
                '-B','-m','coverage','run','--branch','--source=services.mcp_tools','--omit=*/probe.py','-m','pytest','/tests/mcp','-q',
                '-o','cache_dir=/tmp/pytest-cache','--junitxml=/proof/junit.xml']
            c.container('api-tests',command,memory='2g')
            c.log('coverage-export',['docker','run','--rm','--network','none','--memory','256m','--cpus','.5','--mount','type=bind,src='+str(proof)+',dst=/proof',
                  tested['image_id'],'-B','-m','coverage','json','-o','/proof/coverage.json'])
            summary=api_quality(json.loads((proof/'coverage.json').read_bytes()),ET.parse(proof/'junit.xml').getroot())
        else:summary={'scope':'Coordinator configuration and actual child delegation; unchanged MCP APIs tested in their two own jobs.'}
        contract=json.loads((c.out/'contracts.json').read_bytes());c.phase('test',{'contracts':contract,'fresh_api':summary})
    elif phase=='test-host':
        c.log('contracts',[sys.executable,'-B','-m','unittest','tests.test_llm_ci_contract','tests.test_agent_release_ci','-q'])
        cache=None
        if c.role!='coordinator':
            cache=unchanged_quality_source(ROOT)
            for name in ['mcp-quality','mcp-quality-native']:
                c.log('cached-'+name,[sys.executable,'-B','-S',str(ROOT/'scripts'/('verify-'+name+'.py'))])
            score=json.loads((ROOT/'docs/evidence/mcp-quality/run/strict-score.json').read_bytes())
            cache={'source_bindings':cache,'strict_mutation_score_percent':score['strict_score_percent'],'fresh_mutation_run':False,'counts':score['counts']}
        SHARED.save(c.out/'contracts.json',{'passed':True,'source_digest':c.source_digest,'unchanged_mcp_quality_cache':cache})
    elif phase=='build':
        c.prior('test')
        if c.role!='coordinator':
            app=build_overlay(c);archive=c.directory/'mcp-image.tar'
            c.log('image-export',['docker','save','-o',str(archive),app['image']],600)
            with archive.open('rb') as stream:app['imported_config_digest']=docker_config_digest(stream)
            app['export_sha256']=file_hash(archive);app['export_bytes']=archive.stat().st_size
            SHARED.save(c.out/'application.json',app)
        c.native('build-host',cluster=True)
    elif phase=='build-host':package_charts(c)
    elif phase=='deploy':c.prior('build');c.native('deploy-host',cluster=True,docker=True)
    elif phase=='deploy-host':deploy_host(c)
    elif phase=='verify':c.prior('deploy');c.native('verify-host',cluster=True)
    elif phase=='verify-host':verify_host(c)
    elif phase=='rollback':
        if (c.out/'deployment-plan.json').exists():c.native('rollback-host',cluster=True)
    elif phase=='rollback-host':rollback_host(c)
    elif phase=='archive':
        target=ROOT/'outputs/ci-agents';target.mkdir(parents=True,exist_ok=True)
        if c.out.exists():shutil.copytree(c.out,target,dirs_exist_ok=True)
    else:raise ValueError('Unknown agent CI phase')
    c.source_check()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['admission','test','test-host','build','build-host','deploy','deploy-host','verify','verify-host','rollback','rollback-host','archive'])
    run(parser.parse_args().phase)
