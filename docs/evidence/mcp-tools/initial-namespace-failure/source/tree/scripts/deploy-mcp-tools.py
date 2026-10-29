"""Build and verify source-sealed private MCP services on the retained lab VM without provisioning cloud resources."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import time


def run(args):
    """Gate resources/source, keep failed phases and bind actual image, API, snapshot and rollout evidence."""
    root=args.runtime_root.resolve();source=args.source_root.resolve();output=root/'outputs'/args.run
    if os.name!='posix' or os.geteuid()!=0 or not root.is_relative_to('/srv/iot-data') or not source.is_relative_to(root/'data/llm-source'):
        raise ValueError('Use sudo with a sealed source in the retained Linux lab')
    if not re.fullmatch(r'mcp-[a-z0-9][a-z0-9-]{1,40}',args.run):raise ValueError('Invalid MCP run name')
    session=json.loads((root/'data/runtime/ci/session.json').read_bytes())
    remaining=(datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc)).total_seconds()
    memory=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
    if remaining<3600 or memory<16*1024**3 or shutil.disk_usage('/srv/iot-fast').free<30*1024**3:
        raise ValueError('Require one hour remaining, 16 GiB available RAM and 30 GiB free SSD')
    seal=json.loads((source.parent/'manifest.json').read_bytes())
    for name,digest in seal['files'].items():
        path=(source/name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Source seal differs')
    source_digest=sha256(json.dumps(seal,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    tag='mcp-'+source_digest[:12];image='iot-coursework-mcp:'+tag;test_image='iot-coursework-mcp-tests:'+tag
    os.environ['KUBECONFIG']='/etc/rancher/k3s/k3s.yaml'
    k=['/usr/local/bin/k3s','kubectl'];helm=str(root/'data/runtime/kubernetes-assets-27/helm/helm')
    if args.phase in ['build','all']:
        output.mkdir(parents=True,exist_ok=False)
        (output/'source.json').write_text(json.dumps(seal,indent=2)+'\n')
        (output/'budget.json').write_text(json.dumps({'remaining_session_seconds':remaining,'available_memory_bytes':memory,
            'ssd_free_bytes':shutil.disk_usage('/srv/iot-fast').free},indent=2)+'\n')
    elif not output.is_dir():raise ValueError('Inspect a successfully completed build before resuming deployment')

    def command(name,values,timeout=600,check=True,payload=None):
        """Capture original consoles but never persist credential-bearing stdin or request headers."""
        if (output/(name+'.stdout')).exists():raise ValueError('Preserve the existing command receipt: '+name)
        result=subprocess.run(values,input=payload,capture_output=True,timeout=timeout)
        (output/(name+'.stdout')).write_bytes(result.stdout);(output/(name+'.stderr')).write_bytes(result.stderr)
        (output/(name+'.exit')).write_text(str(result.returncode)+'\n')
        if check and result.returncode:raise RuntimeError(name+' failed; inspect the same retained process/artifacts')
        return result

    def get(name,arguments):
        """Read selected Kubernetes objects without dumping Secret data."""
        return json.loads(command(name,k+arguments+['-o','json']).stdout)

    def save(name,value):
        """Create a strict new JSON receipt without replacing an earlier result."""
        with (output/name).open('x') as stream:json.dump(value,stream,indent=2,allow_nan=False)

    if args.phase in ['build','all']:
        base=json.loads(command('base-image',['docker','image','inspect','iot-coursework-rag:deps-c3b696c3955c']).stdout)[0]
        if base['Id']!='sha256:4d65bdfb955f2d459f750fba5f30f381aab0378a4ce95f9047359a5760cd1afd':raise ValueError('The retained dependency image differs')
        for target,name in [('runtime',image),('test',test_image)]:
            command('build-'+target,['docker','build','--target',target,'--label','iot.source.sha256='+source_digest,
                '-f',str(source/'infra/mcp/Dockerfile'),'-t',name,str(source)],timeout=1200)
        inspected=json.loads(command('runtime-image',['docker','image','inspect',image]).stdout)[0]
        tests=output/'tests';tests.mkdir();os.chown(tests,10001,10001)
        native='iot-'+args.run+'-tests'
        parameters=['docker','run','--name',native,'--init','--read-only','--network','none','--cpus=2','--memory=2g','--memory-swap=2g',
            '--cap-drop=ALL','--security-opt=no-new-privileges','--tmpfs','/tmp:rw,nosuid,size=512m',
            '--mount','type=bind,source='+str(tests)+',target=/proof',test_image]
        command('tests',parameters+['-m','coverage','run','--branch','--source=services.mcp_tools','--omit=*/probe.py','-m','pytest','/tests/mcp','-q','-o','cache_dir=/tmp/pytest-cache'],timeout=900)
        container=json.loads(command('tests-container',['docker','inspect',native]).stdout)[0]
        if container['State']['Running'] or container['State']['ExitCode']!=0 or container['State']['OOMKilled']:raise ValueError('Native test process failed')
        command('coverage-json',['docker','run','--rm','--network','none','--read-only','--cpus=1','--memory=512m',
            '--mount','type=bind,source='+str(tests)+',target=/proof',test_image,'-m','coverage','json','-o','/proof/coverage.json'])
        summary=json.loads((tests/'coverage.json').read_bytes())['totals']
        if summary['percent_covered']<=90:raise ValueError('New MCP statement/branch coverage must exceed 90 percent')
        save('build.json',{'passed':True,'image':image,'image_id':inspected['Id'],'source_sha256':source_digest,
                           'coverage':summary,'test_image':test_image})
    if args.phase=='build':return
    build=json.loads((output/'build.json').read_bytes())
    if not build['passed'] or build['source_sha256']!=source_digest or build['image']!=image:raise ValueError('The completed build does not match this deployment')
    info=json.loads(command('image-before-deploy',['docker','image','inspect',image]).stdout)[0]
    if info['Id']!=build['image_id']:raise ValueError('The image changed after testing')
    archive=Path('/srv/iot-fast')/(args.run+'-image.tar')
    if archive.exists():raise ValueError('Preserve the earlier image archive')
    command('image-export',['docker','save','-o',str(archive),image],timeout=600)
    command('image-import',['/usr/local/bin/k3s','ctr','images','import',str(archive)],timeout=600)
    ns=get('namespace-before',['get','namespace','iot-agents','--ignore-not-found'])
    if ns:
        if ns['metadata'].get('labels',{}).get('iot.coursework/module')!='mcp':raise ValueError('Preserve the different existing namespace')
    else:
        body={'apiVersion':'v1','kind':'Namespace','metadata':{'name':'iot-agents','labels':{'iot.coursework/module':'mcp'}}}
        command('namespace-create',k+['apply','-f','-'],payload=json.dumps(body).encode())
    secret_file=root/'data/runtime/secrets/mcp-tools.json'
    if secret_file.exists():credentials=json.loads(secret_file.read_bytes())
    else:
        credentials={role:secrets.token_urlsafe(32) for role in ['retrieval','drift']}
        descriptor=os.open(secret_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w') as stream:json.dump(credentials,stream)
    if set(credentials)!={'retrieval','drift'} or any(len(value)<32 for value in credentials.values()):raise ValueError('Scoped credentials differ')
    snapshot=root/'data/rag/rag-airflow-29c'
    snapshot_files={name:sha256((snapshot/name).read_bytes()).hexdigest() for name in ['knowledge.db','registry.db','chunks.parquet','chunks.json','vectors.json','documents.json']}
    snapshot_name='knowledge-'+args.run
    body={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':snapshot_name,'namespace':'iot-agents'},
          'immutable':True,'data':{'seal.json':json.dumps({'files':snapshot_files},sort_keys=True)}}
    command('snapshot-seal',k+['apply','-f','-'],payload=json.dumps(body).encode());save('snapshot.json',{'directory':str(snapshot),'files':snapshot_files})
    feature=get('feature-service',['get','service','iot-feature-api','-n','iot-features'])
    drift=get('drift-service',['get','service','iot-drift-api','-n','iot-features'])
    feature_url='http://'+feature['spec']['clusterIP'];drift_url='http://'+drift['spec']['clusterIP']
    with urlopen(drift_url+'/health/ready',timeout=10) as response:reference=json.load(response)['reference_id']
    refmap=get('drift-reference',['get','configmap','iot-drift-reference-v1','-n','iot-features'])
    expected=sha256(json.dumps(json.loads(refmap['data']['reference.json']),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    if expected!=reference:raise ValueError('The actual drift reference does not match its source')
    save('reference.json',{'reference_id':reference,'configmap_uid':refmap['metadata']['uid']})
    try:
        with urlopen(feature_url+'/health/ready',timeout=10) as response:feature_ready=response.status==200
    except HTTPError:feature_ready=False
    if not feature_ready:
        command('feature-refresh',['python3',str(source/'scripts/refresh-demo-feature.py'),'--runtime-root',str(root),
            '--runtime-source',str(root/'data/observability-source-2fceb32/tree'),'--run','demo-'+args.run,
            '--output',str(output/'feature-refresh')],timeout=1200)
    chart=str(source/'infra/charts/iot-mcp')
    for role in ['retrieval','drift']:
        release='iot-'+role+'-mcp'
        body={'apiVersion':'v1','kind':'Secret','metadata':{'name':release+'-auth','namespace':'iot-agents'},
              'type':'Opaque','stringData':{'token':credentials[role]}}
        command(role+'-secret',k+['apply','-f','-'],payload=json.dumps(body).encode())
        values={'role':role,'replicas':2,'sourceSha256':source_digest,'revisionMarker':args.run,'image':{'repository':'iot-coursework-mcp','tag':tag},
                'tokenSecret':release+'-auth','referenceId':reference,'snapshot':{'directory':str(snapshot),'sealConfigMap':snapshot_name}}
        if role=='drift':values['resources']={'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'cpu':'1','memory':'512Mi'}}
        path=output/(role+'-values.json');path.write_text(json.dumps(values,indent=2)+'\n')
        command(role+'-install',[helm,'upgrade','--install',release,chart,'-n','iot-agents','-f',str(path),'--atomic','--wait','--timeout','8m'],timeout=600)
        command(role+'-probe',k+['exec','-n','iot-agents','deployment/'+release,'--','python','-m','services.mcp_tools.probe','--role',role,'--run',args.run],timeout=240)
        result=json.loads((output/(role+'-probe.stdout')).read_bytes())
        if not result['passed']:raise ValueError('Native MCP business-tool verification failed')
        values['revisionMarker']=args.run+'-rolling';path=output/(role+'-rolling-values.json');path.write_text(json.dumps(values,indent=2)+'\n')
        command(role+'-rolling',[helm,'upgrade',release,chart,'-n','iot-agents','-f',str(path),'--atomic','--wait','--timeout','5m'])
        failed=command(role+'-bad-upgrade',[helm,'upgrade',release,chart,'-n','iot-agents','-f',str(path),
            '--set','image.tag=deliberately-missing-mcp-image','--atomic','--wait','--timeout','60s'],timeout=240,check=False)
        if failed.returncode==0:raise ValueError('The controlled invalid-image deployment unexpectedly succeeded')
        actual=json.loads(command(role+'-values-after',[helm,'get','values',release,'-n','iot-agents','--all','-o','json']).stdout)
        if actual['image']['tag']!=tag or actual['revisionMarker']!=values['revisionMarker']:raise ValueError('Atomic rollback did not restore the valid release')
        command(role+'-probe-after-rollback',k+['exec','-n','iot-agents','deployment/'+release,'--','python','-m','services.mcp_tools.probe','--role',role,'--run',args.run],timeout=240)
    command('resources-after',k+['get','deployment,pod,service,networkpolicy','-n','iot-agents','-o','json'])
    save('verification.json',{'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'run':args.run,
        'image_id':build['image_id'],'source_sha256':source_digest,'roles':['retrieval','drift'],'replicas_per_role':2,
        'reference_id':reference,'actual_native_mcp_probes':4,'controlled_atomic_rollbacks':2,
        'scope':'Native private MCP/REST tools and actual rollout/fallback. Agent runtime/registry/sandbox, mutation, load, CI and full LLM telemetry remain separate.'})
    print((output/'verification.json').read_text())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root',type=Path,required=True);parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--run',required=True);parser.add_argument('--phase',choices=['build','deploy','all'],default='all')
    run(parser.parse_args())
