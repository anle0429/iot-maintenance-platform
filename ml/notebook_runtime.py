"""Run notebook stages in the already verified, separate Feast and Spark images on Linux."""
import json,os,re,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
from shutil import disk_usage
from ml.training_contract import digest,save


class NotebookRuntime:
    """Bound Docker stages while keeping incompatible SDKs outside the Jupyter kernel."""
    def __init__(self,root,run,deadline_utc):
        """Admit a fresh workspace only with actual memory/disk/time and existing local images."""
        self.root=Path(root).resolve();self.run=run
        self.deadline=datetime.fromisoformat(deadline_utc.replace('Z','+00:00'))
        if os.name!='posix' or not re.fullmatch('[a-z][a-z0-9-]{2,39}',run):raise ValueError('Use Linux and a safe fresh run name')
        self.guard(5400)
        self.workspace=self.root/'data/ml-workspaces'/run;self.output=self.root/'outputs/notebook'/run
        self.scratch=Path('/srv/iot-fast/ml-scratch')/run
        for p in [self.workspace,self.output,self.scratch]:
            if p.exists():raise ValueError('Preserve previous notebook execution: '+str(p))
        self.images={name:subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',tag],text=True).strip()
            for name,tag in [('feast','iot-coursework-feast:0.67.0'),('spark','iot-coursework-spark:3.5.9')]}
        for p in [self.workspace,self.output,self.scratch]:p.mkdir(parents=True);p.chmod(0o1777)
        self.base='/ml-data'
        save(self.output/'preflight.json',{'run':run,'checked_utc':datetime.now(timezone.utc).isoformat(),
            'deadline_utc':deadline_utc,'images':self.images,'code_sha256':{n:digest(self.root/n) for n in
            ['ml/notebook_load.py','ml/snapshot.py','ml/notebook_runtime.py','ml/training_job.py','ml/training_contract.py','ml/protocols/medium-60d-v1.json']},
            'scope':'Executed Jupyter cells, native Feast and bounded local[2] Spark; not distributed training.'})

    def guard(self,minimum_seconds=600):
        """Require reserves before each stage rather than trusting the previous boot's measurements."""
        available=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024
        if (self.deadline.tzinfo is None or (self.deadline-datetime.now(timezone.utc)).total_seconds()<minimum_seconds
            or available<16*1024**3 or min(disk_usage(p).free for p in ['/srv/iot-data','/srv/iot-fast'])<30_000_000_000):
            raise ValueError('Notebook time/memory/disk reserve is insufficient')

    def container(self,stage,image,command):
        """Execute a fresh unprivileged container with no Docker socket and preserve its real output."""
        self.guard();name=self.run+'-'+stage;console=self.output/(stage+'-console.txt')
        args=['docker','run','--name',name,'--cpus','2','--memory','10g','--memory-swap','10g',
            '--network','iot-feast_default','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--env','PYTHONPATH=/workspace','--env','FEAST_USAGE=False','--env','IOT_FEAST_REDIS=online:6379',
            '--env','IOT_FEAST_TEMP=/ml-scratch/offline','--env','SPARK_LOCAL_IP=127.0.0.1',
            '--env','OPENBLAS_NUM_THREADS=1','--env','OMP_NUM_THREADS=1',
            '--mount','type=bind,src='+str(self.root)+',dst=/workspace,readonly',
            '--mount','type=bind,src='+str(self.root/'data/feast')+',dst=/workspace/data/feast',
            '--mount','type=bind,src='+str(self.workspace)+',dst=/ml-data',
            '--mount','type=bind,src='+str(self.scratch)+',dst=/ml-scratch',
            '--entrypoint',command[0],self.images[image],*command[1:]]
        started=time.monotonic()
        with console.open('w') as stream:
            try:result=subprocess.run(args,stdout=stream,stderr=subprocess.STDOUT,timeout=2400)
            except BaseException:
                subprocess.run(['docker','stop','--time','15',name],capture_output=True);raise
        state=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
        save(self.output/(stage+'-container.json'),{'name':name,'id':state['Id'],'image':state['Image'],
            'user':state['Config']['User'],'memory':state['HostConfig']['Memory'],'nano_cpus':state['HostConfig']['NanoCpus'],
            'exit_code':state['State']['ExitCode'],'oom_killed':state['State']['OOMKilled'],'seconds':time.monotonic()-started,
            'started_utc':state['State']['StartedAt'],'finished_utc':state['State']['FinishedAt'],'command':command})
        if result.returncode:raise RuntimeError(console.read_text()[-7000:])
        print(stage+': exit0; '+str(round(time.monotonic()-started,3))+' seconds')

    def load(self):
        """Export labels and retrieve every eligible snapshot through the actual registered Feast service."""
        self.container('load','feast',['python','-m','ml.notebook_load','--workspace','/ml-data/input',
            '--output','/ml-data/cohort.json','--protocol','/workspace/ml/protocols/medium-60d-v1.json',
            '--state','/workspace/data/feast/batch-medium-27','--batch-report','/workspace/outputs/feast-batch-27/batch/report.json',
            '--artifacts','/workspace/data/feature-datasets/feature-medium-27-v2/artifacts',
            '--feature-report','/workspace/outputs/feature-medium-27-v2/feature-report.json'])
        return self.read('input/load-report.json')

    def spark(self,stage):
        """Use exactly the frozen split and train/validation kernels also used in KFP components."""
        source,target={'split':('cohort.json','partitions.json'),'train':('partitions.json','selection.json')}[stage]
        self.container(stage,'spark',['/opt/spark/bin/spark-submit','--master','local[2]','--driver-memory','6g',
            '--conf','spark.driver.maxResultSize=128m','--conf','spark.local.dir=/ml-scratch',
            '/workspace/ml/training_job.py','--step',stage,'--workspace','/ml-data',
            '--protocol','/workspace/ml/protocols/medium-60d-v1.json','--input','/ml-data/'+source,'--output','/ml-data/'+target])
        return self.read(target)

    def read(self,name):
        """Display native small receipts without copying model binaries or full data into cell output."""
        return json.loads((self.workspace/name).read_text())

    def validate_saved_model(self):
        """Reload both model formats and check validation, keeping test data closed."""
        self.container('validate','spark',['/opt/spark/bin/spark-submit','--master','local[2]','--driver-memory','6g',
            '--conf','spark.driver.maxResultSize=128m','--conf','spark.local.dir=/ml-scratch',
            '/workspace/ml/notebook_validate.py','--selection','/ml-data/selection.json',
            '--protocol','/workspace/ml/protocols/medium-60d-v1.json','--output','/ml-data/validation-reload.json'])
        return self.read('validation-reload.json')
