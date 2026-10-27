"""Test, build, automatically deploy and verify a fresh source-bound KFP training release from Jenkins."""
import argparse
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.ci_training_contract import identity,admission,verify_result
from ml.versioned_contract import SOURCE_FILES


def digest(path):
    """Bind small source and evidence files to their actual bytes."""
    return sha256(path.read_bytes()).hexdigest()


def save(path,value):
    """Replace live CI snapshots atomically without writing credentials."""
    path.parent.mkdir(parents=True,exist_ok=True);pending=path.with_suffix('.pending')
    pending.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');pending.replace(path)


def exported_config_digest(tag):
    """Hash the actual Docker-save config blob; Docker29 image ID can name a manifest instead of this blob."""
    exporter=subprocess.Popen(['docker','save',tag],stdout=subprocess.PIPE)
    small={};manifest=None
    try:
        with tarfile.open(fileobj=exporter.stdout,mode='r|') as archive:
            for item in archive:
                if not item.isfile() or item.size>1_000_000:continue
                raw=archive.extractfile(item).read();small[item.name]=sha256(raw).hexdigest()
                if item.name=='manifest.json':manifest=json.loads(raw)
        if exporter.wait()!=0:raise RuntimeError('Cannot read the verified image export')
    finally:
        exporter.stdout.close()
        if exporter.poll() is None:exporter.terminate();exporter.wait()
    if not manifest or len(manifest)!=1 or manifest[0]['Config'] not in small:
        raise ValueError('Docker export lacks a unique bounded config blob')
    return 'sha256:'+small[manifest[0]['Config']]


def controller(image,deploy,source,extra,command,network='none',writable=False):
    """Use a short-lived bounded SDK container; kubeconfig is mounted only when requested, never copied."""
    values=['docker','run','--rm','--name',extra['name'],'--memory','2g','--memory-swap','2g','--cpus','1',
        '--network',network,'--entrypoint','python','--env','PYTHONPATH=/src',
        '--mount',f'type=bind,src={source},dst=/src,readonly',
        '--mount',f'type=bind,src={deploy},dst={deploy}'+('' if writable else ',readonly')]
    for name in ['IOT_CI_COMMIT','BUILD_NUMBER','IOT_CI_PROVIDER','JENKINS_URL','IOT_DEPLOY_ROOT']:
        values+=['--env',name]
    values+=extra.get('arguments',[])
    return values+[image,*command]


def run(phase):
    """Seal every phase to its source commit and fail CI unless this newly submitted run produces native receipts."""
    release=identity(os.environ['BUILD_NUMBER']);commit=os.environ['IOT_CI_COMMIT']
    if os.environ.get('IOT_CI_PROVIDER')!='jenkins' or not os.environ.get('JENKINS_URL'):
        raise ValueError('Only the existing trusted private Jenkins job may deploy')
    deploy=Path(os.environ['IOT_DEPLOY_ROOT']).resolve();directory=deploy/'data/ci/releases'/release
    seal=json.loads((deploy/'data/ci/source'/commit/'manifest.json').read_text())
    if seal['commit']!=commit:
        raise ValueError('CI source commit differs from the sealed packet')
    for name,h in seal['files'].items():
        path=(ROOT/name).resolve()
        if not path.is_relative_to(ROOT) or digest(path)!=h:
            raise ValueError('Sealed source changed: '+name)
    out=ROOT/'outputs/ci-training';out.mkdir(parents=True,exist_ok=True)
    session=json.loads((deploy/'data/runtime/ci/session.json').read_text())
    if phase=='admission':
        ram=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024
        save(out/'admission-attempt.json',{'checked_utc':datetime.now(timezone.utc).isoformat(),
            'auto_stop_utc':session['auto_stop_utc'],'ram_available_bytes':ram,
            'disk_free_bytes':[shutil.disk_usage(deploy).free,shutil.disk_usage('/srv/iot-fast/ci').free]})
        reserves=admission(session['auto_stop_utc'],datetime.now(timezone.utc),ram,
            [shutil.disk_usage(deploy).free,shutil.disk_usage('/srv/iot-fast/ci').free])
        # Leave ten extra minutes for tests/image import before the unchanged runner90minute gate.
        if reserves['seconds_before_actual_stop']<6000:
            raise ValueError('CI needs100minutes initially, including10minutes preparation and90minutes native admission')
        save(out/'admission.json',reserves)
    elif phase=='test':
        subprocess.run(['python3','-m','unittest','tests.test_ci_training','tests.test_automatic_versions',
            'tests.test_data_versions','tests.test_model_data_link','tests.test_notebook_holdout_gate','-v'],cwd=ROOT,check=True)
        for name in seal['files']:
            if name.endswith('.py'):compile((ROOT/name).read_text(),name,'exec')
        save(out/'test.json',{'passed':True,'commit':commit,'source_files':len(seal['files'])})
    elif phase=='build':
        if not json.loads((out/'test.json').read_text())['passed']:
            raise ValueError('Test stage must pass before build')
        directory.mkdir(parents=True,exist_ok=False);(directory/'outputs').mkdir();(directory/'outputs').chmod(0o1777)
        controller_tag='iot-ci-training-controller:'+release
        subprocess.run(['docker','build','-f',str(ROOT/'infra/ci/TrainingController.Dockerfile'),'-t',controller_tag,str(ROOT)],check=True)
        control_id=subprocess.check_output(['docker','image','inspect',controller_tag,'--format','{{.Id}}'],text=True).strip()
        sources={n:h for n,h in seal['files'].items() if n.startswith(('ml/','feature_store/','infra/kfp/','infra/ml-versioning/','notebooks/'))
            or n in SOURCE_FILES}
        images={}
        for kind,runtime in [('feast','iot-coursework-feast-versioned:0.67.0-3.17.2'),('spark','iot-coursework-spark-registry:3.5.9-3.17.2')]:
            base=subprocess.check_output(['docker','image','inspect',runtime,'--format','{{.Id}}'],text=True).strip()
            tag='iot-ci-training-'+kind+':'+release
            subprocess.run(['docker','build','--build-arg','RUNTIME='+runtime,'--label','org.opencontainers.image.revision='+commit,
                '-f',str(ROOT/'infra/ci/Training.Dockerfile'),'-t',tag,str(ROOT)],check=True)
            image=json.loads(subprocess.check_output(['docker','image','inspect',tag],text=True))[0]
            probe="import json,pathlib,hashlib; r=pathlib.Path('/opt/iot-release'); print(json.dumps({str(p.relative_to(r)):hashlib.sha256(p.read_bytes()).hexdigest() for p in r.rglob('*') if p.is_file() and '__pycache__' not in p.parts}))"
            packaged=json.loads(subprocess.check_output(['docker','run','--rm','--network','none','--cpus','.5','--memory','256m','--entrypoint','python3',image['Id'],'-c',probe],text=True))
            if packaged!=sources or image['Config']['Labels']['org.opencontainers.image.revision']!=commit:
                raise ValueError('Built training source differs from tested source')
            exporter=subprocess.Popen(['docker','save',tag],stdout=subprocess.PIPE)
            try:
                subprocess.run(['docker','run','--rm','-i','--network','none','--cpus','1','--memory','1g',
                    '--mount','type=bind,src=/usr/local/bin/k3s,dst=/usr/local/bin/k3s,readonly',
                    '--mount','type=bind,src=/run/k3s/containerd/containerd.sock,dst=/run/k3s/containerd/containerd.sock',
                    '--entrypoint','/usr/local/bin/k3s',control_id,'ctr','images','import','-'],stdin=exporter.stdout,check=True)
            finally:
                exporter.stdout.close()
                if exporter.wait()!=0:raise RuntimeError('Docker image export failed')
            cri=json.loads(subprocess.check_output(['docker','run','--rm','--network','none','--cpus','1','--memory','1g',
                '--mount','type=bind,src=/usr/local/bin/k3s,dst=/usr/local/bin/k3s,readonly',
                '--mount','type=bind,src=/run/k3s/containerd/containerd.sock,dst=/run/k3s/containerd/containerd.sock',
                '--entrypoint','/usr/local/bin/k3s',control_id,'crictl','--config=/dev/null',
                '--runtime-endpoint=unix:///run/k3s/containerd/containerd.sock',
                '--image-endpoint=unix:///run/k3s/containerd/containerd.sock','inspecti',tag],text=True))
            config_digest=exported_config_digest(tag)
            if cri['status']['id']!=config_digest or subprocess.check_output([
                    'docker','image','inspect',tag,'--format','{{.Id}}'],text=True).strip()!=image['Id']:
                raise ValueError('Kubernetes runtime config differs from the actual verified Docker export')
            images[kind]={'id':image['Id'],'tag':tag,'runtime_base_id':base,'packaged_source_verified':True,
                'containerd_imported':True,'docker_export_config_digest':config_digest,'cri_image_id':cri['status']['id']}
        manifest={'commit':commit,'release':release,'controller_image_id':control_id,'images':images,
            'runtime_source_sha256':sources,'source_sha256':{n:seal['files'][n] for n in SOURCE_FILES}}
        save(directory/'build.json',manifest);save(out/'build.json',manifest)
        command=controller(control_id,deploy,ROOT,{'name':release+'-compile'},
            ['/src/scripts/compile-ci-training.py','--manifest',str(directory/'build.json'),'--output',str(directory/'pipeline.yaml')],
            writable=True)
        subprocess.run(command,check=True)
        save(out/'compiled.json',{'passed':True,'pipeline_sha256':digest(directory/'pipeline.yaml'),'tasks':5})
    elif phase=='deploy':
        build=json.loads((directory/'build.json').read_text())
        running=[json.loads(line) for line in subprocess.check_output(['docker','ps','--format','json'],text=True).splitlines()]
        notebook_names=[p.parent.name for p in (deploy/'outputs/notebook').glob('*/preflight.json')]
        if any(x['Names'].startswith(name+'-') for x in running for name in notebook_names) or any(
                'iot.pipeline=feast-materialization' in x.get('Labels','') for x in running):
            raise ValueError('Preserve the executable snapshot of the active notebook')
        extra={'name':release+'-deploy','arguments':[
            '--mount','type=bind,src=/usr/local/bin/k3s,dst=/usr/local/bin/k3s,readonly',
            '--mount','type=bind,src=/etc/rancher/k3s/k3s.yaml,dst=/etc/rancher/k3s/k3s.yaml,readonly']}
        subprocess.run(controller(build['controller_image_id'],deploy,ROOT,extra,
            ['/src/scripts/ci-training.py','deploy-host'],network='host',writable=True),check=True)
        shutil.copyfile(directory/'deployment.json',out/'deployment.json')
    elif phase=='deploy-host':
        build=json.loads((directory/'build.json').read_text());before={}
        # Do not replace executable source while a previous training controller is live.
        pods=json.loads(subprocess.check_output(['/usr/local/bin/k3s','kubectl','get','pods','-n','kubeflow','-o','json'],text=True))
        if any(p.get('status',{}).get('phase') in ['Pending','Running'] and any(
                k in p.get('metadata',{}).get('labels',{}) for k in ['pipeline/runid','iot-coursework-run']) for p in pods['items']):
            raise ValueError('Preserve executable source while a native KFP run or executor is active')
        for name,h in build['runtime_source_sha256'].items():
            target=deploy/name;target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists() and digest(target)!=h:
                backup=directory/'source-before'/name;backup.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(target,backup)
                before[name]=digest(target)
            pending=target.with_suffix(target.suffix+'.ci-pending');shutil.copyfile(ROOT/name,pending);pending.chmod(0o644);pending.replace(target)
            if digest(target)!=h:raise ValueError('Deployed source readback differs: '+name)
        binding={'format':'iot-source-snapshot-v1','commit':commit,'files':build['source_sha256'],
            'parent_data_version':'5e78230e8defc2c584d0b97f1e438b65e2659d7358ca44cdda1d6dd95e6db30e',
            'tracking_uri':'http://tracking:5000','scope':'Exact committed CI execution source copied to the existing VM; no inferred VM Git HEAD.'}
        save(directory/'source-snapshot.json',binding)
        pointer=deploy/'data/runtime/ml-versioning/source-snapshot.json'
        backup=directory/'source-before/previous-source-snapshot.json';backup.parent.mkdir(parents=True,exist_ok=True)
        if pointer.exists():shutil.copyfile(pointer,backup)
        save(pointer,binding)
        save(directory/'deployment.json',{'passed':True,'commit':commit,'release':release,
            'pipeline_path':str(directory/'pipeline.yaml'),'pipeline_sha256':digest(directory/'pipeline.yaml'),
            'source_sha256':build['source_sha256'],'images':build['images'],'previous_changed_source_sha256':before,
            'deployed_utc':datetime.now(timezone.utc).isoformat()})
    elif phase=='cleanup':
        ids=subprocess.check_output(['docker','ps','-q','--filter','name=^/'+release+'-controller$'],text=True).split()
        if ids:subprocess.run(['docker','stop','--time','20',*ids],check=True)
        submission=deploy/'outputs/versioned-training'/release/'submission.json'
        if submission.exists() and (directory/'build.json').exists():
            build=json.loads((directory/'build.json').read_text())
            extra={'name':release+'-cleanup','arguments':[
                '--mount','type=bind,src=/usr/local/bin/k3s,dst=/usr/local/bin/k3s,readonly',
                '--mount','type=bind,src=/etc/rancher/k3s/k3s.yaml,dst=/etc/rancher/k3s/k3s.yaml,readonly',
                '--mount',f'type=bind,src={directory},dst={directory}']}
            subprocess.run(controller(build['controller_image_id'],deploy,ROOT,extra,
                ['/src/scripts/ci-training.py','cleanup-host'],network='host'),check=True)
        save(out/'cleanup.json',{'release':release,'stopped_only_own_controller':ids,
            'scope':'No blanket pod/container deletion. Inspect and terminate only this release submission when backend remains active.'})
    elif phase=='cleanup-host':
        import kfp,time
        submission=json.loads((deploy/'outputs/versioned-training'/release/'submission.json').read_text())
        service=json.loads(subprocess.check_output(['/usr/local/bin/k3s','kubectl','get','service','ml-pipeline','-n','kubeflow','-o','json'],text=True))
        client=kfp.Client(host='http://'+service['spec']['clusterIP']+':8888',namespace='kubeflow')
        actual=client.get_run(submission['run_id']).to_dict()
        if actual['display_name']!=release:
            raise ValueError('Refuse cleanup of a backend run belonging to another release')
        terminal=['SUCCEEDED','FAILED','CANCELED','CANCELLED','SKIPPED']
        previous=actual['state'];requested=False
        if str(previous).upper() not in terminal:
            client.terminate_run(submission['run_id']);requested=True
            for _ in range(12):
                actual=client.get_run(submission['run_id']).to_dict()
                if str(actual['state']).upper() in terminal:break
                time.sleep(5)
        save(directory/'outputs/cleanup-backend.json',{'run_id':submission['run_id'],'release':release,
            'previous_state':previous,'actual_state':actual['state'],'termination_requested':requested,
            'checked_utc':datetime.now(timezone.utc).isoformat()})
    else:
        build=json.loads((directory/'build.json').read_text());started=datetime.now(timezone.utc).isoformat()
        extra={'name':release+'-controller','arguments':[
            '--env','PYTHONPATH='+str(deploy),
            '--mount','type=bind,src=/srv/iot-data,dst=/srv/iot-data,readonly',
            '--mount','type=bind,src=/srv/iot-fast,dst=/srv/iot-fast,readonly',
            '--mount','type=bind,src=/usr/local/bin/k3s,dst=/usr/local/bin/k3s,readonly',
            '--mount','type=bind,src=/etc/rancher/k3s/k3s.yaml,dst=/etc/rancher/k3s/k3s.yaml,readonly',
            '--mount',f'type=bind,src={deploy}/outputs/versioned-training,dst={deploy}/outputs/versioned-training',
            '--mount',f'type=bind,src={deploy}/data/ml-workspaces/training-cluster-27,dst={deploy}/data/ml-workspaces/training-cluster-27']}
        command=controller(build['controller_image_id'],deploy,ROOT,extra,
            [str(deploy/'scripts/run-versioned-training.py'),'--run',release,'--deadline-utc',session['auto_stop_utc'],
             '--source-binding',str(directory/'source-snapshot.json'),'--deployment-manifest',str(directory/'deployment.json'),
             '--oracle','outputs/distributed-oracle/distributed-smoke-27-v2/verification.json'],network='host')
        with (out/'training-controller.txt').open('w') as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=3900)
        native=deploy/'outputs/versioned-training'/release
        workspace=deploy/'data/ml-workspaces/training-cluster-27'/release
        read=lambda name:json.loads((native/name).read_text())
        data=json.loads((workspace/'lineage/automatic-data.json').read_text());model=json.loads((workspace/'lineage/registration.json').read_text())
        proof=verify_result(read('preflight.json'),read('submission.json'),read('verification.json'),data,model,commit,started)
        if not read('distributed-verification.json')['passed'] or not (native/'two-executors-running.json').exists():
            raise ValueError('CI training lacks two working native executors')
        runtime_by_tag={v['tag']:v['cri_image_id'] for v in build['images'].values()};mains=[]
        for pod in read('pipeline-pods.json')['items']:
            statuses={x['name']:x for x in pod.get('status',{}).get('containerStatuses',[])}
            for container in pod['spec']['containers']:
                tag=container['image']
                if tag not in runtime_by_tag:continue
                status=statuses[container['name']]
                if status['state']['terminated']['exitCode']!=0 or status['imageID']!=runtime_by_tag[tag]:
                    raise ValueError('Native KFP main container did not run the verified image successfully')
                mains.append({'pod':pod['metadata']['name'],'image':tag,'image_id':status['imageID'],'exit_code':0})
        if len(mains)!=5:raise ValueError('Require all five native ML main containers')
        save(out/'verified-main-containers.json',{'passed':True,'containers':mains})
        for name,h in build['runtime_source_sha256'].items():
            if digest(deploy/name)!=h:raise ValueError('Source changed during CI execution: '+name)
        for name in ['preflight.json','submission.json','verification.json','distributed-verification.json','ci-deployment.json']:
            shutil.copyfile(native/name,out/('native-verification.json' if name=='verification.json' else name))
        for path in (workspace/'lineage').glob('*.json'):
            target=out/'lineage'/path.name;target.parent.mkdir(exist_ok=True);shutil.copyfile(path,target)
        save(out/'verification.json',{**proof,'commit':commit,'release':release,'checked_utc':datetime.now(timezone.utc).isoformat(),
            'scope':'Fresh five-task KFP run from Jenkins tested/built/deployed source, actual two-executor fit, automatic data versioning and model-only candidate registration. No production promotion.'})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['admission','test','build','deploy','deploy-host','verify','cleanup','cleanup-host'])
    run(parser.parse_args().phase)
