"""Reuse the frozen five data tasks and publish CI governance through a separately versioned metadata entry point."""
from pathlib import Path
import importlib.util
import json
import os


def execute(operation,run_id):
    """Preserve old manual pipeline semantics while binding CI publication to its actual DAG and source."""
    spec=importlib.util.spec_from_file_location('iot_ci_base_tasks',Path(__file__).with_name('airflow_tasks.py'))
    base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
    if operation!='publish':return base.execute(operation,run_id)
    import docker
    root=Path(os.environ['IOT_ROOT']).resolve();manifest,source,state,reports=base.load_release(root,run_id)
    client=docker.from_env()
    try:
        container=client.containers.create(manifest['metadata_image_id'],['--state','/state','--run',run_id],
            entrypoint=['/usr/bin/timeout','--signal=TERM','--kill-after=10s','600','python','-B','-m','rag.ci_metadata'],
            name='iot-'+run_id+'-publish',working_dir='/workspace',
            environment={'PYTHONPATH':'/workspace','DATAHUB_GMS_URL':'http://datahub-gms:8080',
                         'IOT_CI_COMMIT':manifest['ci_source_commit'],'IOT_CI_RELEASE':manifest['ci_release']},
            volumes={str(source):{'bind':'/workspace','mode':'ro'},str(state):{'bind':'/state','mode':'rw'}},
            network=manifest['metadata_network_id'],user='10001:10001',read_only=True,
            mem_limit='2g',memswap_limit='2g',nano_cpus=1_000_000_000,pids_limit=256,
            cap_drop=['ALL'],security_opt=['no-new-privileges'],tmpfs={'/tmp':'rw,nosuid,size=512m'},init=True,
            labels={'iot.rag.run':run_id,'iot.rag.operation':'publish'})
        container.start();print(json.dumps({'container_id':container.id,'operation':operation}),flush=True)
        result=container.wait(timeout=660);container.reload();logs=container.logs()
        (reports/'publish.log').write_bytes(logs)
        with (reports/'publish-container.json').open('x') as stream:json.dump(container.attrs,stream,indent=2)
        print(logs.decode(errors='replace'),flush=True)
        if result['StatusCode']!=0 or container.attrs['State']['OOMKilled']:raise RuntimeError('Preserve and inspect the failed CI metadata task')
        return {'operation':operation,'container_id':container.id,'image_id':container.attrs['Image']}
    finally:client.close()
