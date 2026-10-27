"""Run a bounded real Kafka/Flink fixture beside a native Feast push worker on the existing Linux VM."""

import argparse
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from shutil import disk_usage
import signal
import subprocess
import sys
import threading
import time
from urllib.request import urlopen


ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from feature_store.outbox import window_payload
from pipelines.stream_contract import fixture_phases,volume_store_path,window_truth
from pipelines.stream_verification import StreamRunner,compare_windows


def save(path,value):
    """Write small controller evidence atomically, outside generated data and credentials."""
    path.parent.mkdir(parents=True,exist_ok=True)
    pending=path.with_suffix('.pending')
    pending.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');pending.replace(path)


def digest(path):
    """Hash only bounded code/report files; this job never rehashes the100GB source."""
    return sha256(path.read_bytes()).hexdigest()


def semantic_digest(rows):
    """Bind the final absolute windows independently of input order and transport timestamps."""
    values=sorted((window_payload(row)[0],window_payload(row)[1]) for row in rows)
    return sha256(json.dumps(values,separators=(',',':')).encode()).hexdigest()


def available_ram():
    """Read actual Linux available RAM rather than treating Docker limits as current usage."""
    return int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024


def idle_cpu_cores():
    """Sample CPU headroom before admitting two additional bounded service cores."""
    def sample():
        values=[int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
        return sum(values),values[3]+values[4]
    total,idle=sample();time.sleep(1);after,new_idle=sample()
    return (new_idle-idle)/(after-total)*(os.cpu_count() or 1)


def run(args):
    """Keep source/worker lifecycle explicit; require a push observed before the actual Flink job finishes."""
    os.chdir(ROOT)
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,39}',args.run):
        raise ValueError('Choose a safe new lowercase run name')
    deadline=datetime.fromisoformat(args.deadline_utc.replace('Z','+00:00'))
    if deadline.tzinfo is None or (deadline-datetime.now(timezone.utc)).total_seconds()<1200:
        raise ValueError('At least20minutes must remain before the actual VM stop deadline')
    proof=json.loads(args.native_proof.read_text())
    if not proof['passed']:
        raise ValueError('Complete the native dual-store boundary checks first')
    parent=args.native_proof.parent
    for name,h in json.loads((parent/'preflight.json').read_text())['code_sha256'].items():
        if digest(ROOT/name)!=h:
            raise ValueError('Native push code changed since its store-boundary verification')
    fence=json.loads(args.ingest_fence.read_text())['execution_sha256'] if args.ingest_fence else {}
    if any(digest(ROOT/n)!=h for n,h in fence.items()):
        raise ValueError('Active ingest execution bytes changed')
    if available_ram()<20*1024**3 or disk_usage('/srv/iot-fast').free<40_000_000_000 or disk_usage('/srv/iot-data').free<35_000_000_000:
        raise ValueError('Insufficient RAM/SSD/HDD reserve for this bounded concurrent fixture')
    idle=idle_cpu_cores()
    if idle<2.25:
        raise ValueError('Less than2.25idleCPU cores; keep the large Spark job first and retry this fixture later')
    runner=StreamRunner('docker',args.run,ui_pause_seconds=45)
    runner.base+=['-f','infra/streaming.cloud.compose.yaml']
    out=ROOT/runner.output/'feast';out.mkdir();out.chmod(0o1777)
    state=ROOT/'data/feast'/('stream-'+args.run)
    if state.exists():
        raise ValueError('Preserve existing stream push state and choose another run name')
    images={n:subprocess.check_output(['docker','image','inspect',n,'--format','{{.Id}}'],text=True).strip()
            for n in ('iot-coursework-feast:0.67.0','iot-coursework-flink:1.20.3')}
    source_id='kafka-flink-finalized-'+args.run
    exported=Path('/workspace')/runner.data/'corrected.sqlite'
    live_database=volume_store_path(exported)
    worker_name='iot-feast-push-'+args.run
    flink_name='iot-stream-'+args.run+'-corrected'
    kafka_id=subprocess.check_output(runner.base+['ps','-q','kafka'],text=True).strip()
    if kafka_id:
        raise ValueError('Existing coursework Kafka state is active; inspect it before taking lifecycle ownership')
    pre={'checked_utc':datetime.now(timezone.utc).isoformat(),'images':images,'available_ram_bytes':available_ram(),
         'idle_cpu_cores_sample':idle,'source_id':source_id,'source_live_database':str(live_database),
         'caps':{'flink_cpu':1,'flink_memory_bytes':3*1024**3,'kafka_cpu':0.5,'kafka_memory_bytes':768*1024**2,'feast_cpu':0.5,'feast_memory_bytes':8*1024**3},
         'code_sha256':{n:digest(ROOT/n) for n in ['feature_store/stream_worker.py','feature_store/push.py','feature_store/outbox.py','pipelines/stream_job.py','pipelines/stream_contract.py','pipelines/stream_verification.py','infra/streaming.compose.yaml','infra/streaming.cloud.compose.yaml','scripts/run-stream-push.py']}}
    save(out/'preflight.json',pre)
    stopped=threading.Event();live=[];guard=[];worker=None;observer=None;started_kafka=False
    def terminate(*_):
        """Stop only these two owned workload containers; the surrounding platform/Spark remain running."""
        for name in (worker_name,flink_name):subprocess.run(['docker','stop','-t','10',name],capture_output=True)
        raise RuntimeError('Bounded source/push controller stopped; retain all reports and intents')
    signal.signal(signal.SIGTERM,terminate)
    def observe():
        """Record actual coexistence of acknowledged windows and a still-running Flink source job."""
        while not stopped.wait(0.5):
            if worker is not None and worker.poll() is not None and not (out/'SOURCE_DONE.json').exists():
                guard.append('Own push worker exited before the source completed');os.kill(os.getpid(),signal.SIGTERM);return
            if (available_ram()<10*1024**3 or disk_usage('/srv/iot-fast').free<35_000_000_000
                    or disk_usage('/srv/iot-data').free<30_000_000_000 or (deadline-datetime.now(timezone.utc)).total_seconds()<300):
                guard.append('Own live fixture resource/deadline guard');os.kill(os.getpid(),signal.SIGTERM);return
            progress=out/'progress.json'
            if not live and progress.is_file() and json.loads(progress.read_text())['completed_unique_windows']>0:
                result=subprocess.run(['docker','inspect',worker_name,flink_name],capture_output=True,text=True)
                if result.returncode==0:
                    containers=json.loads(result.stdout)
                    if all(x['State']['Running'] for x in containers):
                        try:
                            with urlopen('http://127.0.0.1:18081/jobs/overview',timeout=5) as response:overview=json.load(response)
                        except OSError:continue
                        if any(j['state']=='RUNNING' for j in overview['jobs']):
                            value={'checked_utc':datetime.now(timezone.utc).isoformat(),'worker_progress':json.loads(progress.read_text()),
                                   'flink_rest':overview,'post_job_export_not_yet_present':not (ROOT/runner.data/'corrected.sqlite').exists(),
                                   'containers':[{'name':x['Name'],'image_id':x['Image'],'running':x['State']['Running'],'memory_bytes':x['HostConfig']['Memory'],'nano_cpus':x['HostConfig']['NanoCpus']} for x in containers]}
                            live.append(value);save(out/'observed-live-push.json',value)
    try:
        subprocess.run(runner.base+['up','-d','--wait','kafka'],check=True);started_kafka=True
        # Seed the shared volume from the Flink image so its writer UID owns the sink directory.
        identity=json.loads(subprocess.check_output(['docker','run','--rm','--memory=512m','--cpus=0.5','--volume',
                        'iot-coursework-stream_ledger-data:/var/lib/iot-ledgers',images['iot-coursework-flink:1.20.3'],
                        '-c','import os,json; assert os.access("/var/lib/iot-ledgers",os.W_OK); print(json.dumps({"uid":os.getuid(),"gid":os.getgid()}))'],text=True))
        state.mkdir();os.chown(state,identity['uid'],identity['gid'])
        os.chown(ROOT/runner.data,identity['uid'],identity['gid'])
        os.chown(ROOT/runner.output,identity['uid'],identity['gid'])
        pre['sqlite_reader_writer_identity']=identity
        pre['source_sql_readonly_with_writable_wal_coordination']=True
        save(out/'preflight.json',pre)
        cmd=['docker','run','--rm','--name',worker_name,'--memory=8g','--cpus=0.5','--network=iot-feast_default',
             '--user',str(identity['uid'])+':'+str(identity['gid']),
             '--env','PYTHONPATH=/workspace','--env','IOT_FEAST_REDIS=online:6379','--env','FEAST_USAGE=False',
             '--volume',str(ROOT/'feature_store')+':/workspace/feature_store:ro',
             '--volume',str(ROOT/'data/feast')+':/workspace/data/feast',
             '--volume',str(ROOT/'outputs')+':/workspace/outputs',
             '--volume','iot-coursework-stream_ledger-data:/var/lib/iot-ledgers',
             images['iot-coursework-feast:0.67.0'],'-m','feature_store.stream_worker','--database',str(live_database),
             '--state','/workspace/data/feast/'+state.name,'--source-id',source_id,
             '--report','/workspace/'+runner.output.as_posix()+'/feast/report.json',
             '--source-done','/workspace/'+runner.output.as_posix()+'/feast/SOURCE_DONE.json','--max-seconds','600']
        with (out/'worker-console.txt').open('w') as log:
            worker=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
            observer=threading.Thread(target=observe,daemon=True);observer.start()
            # The source must not outrun a cold SDK process before its registry/providers are ready.
            ready_started=time.monotonic()
            while not (out/'progress.json').is_file():
                if worker.poll() is not None:
                    raise RuntimeError('Push worker exited before its actual readiness progress was available')
                if time.monotonic()-ready_started>300:
                    raise TimeoutError('Push worker did not become ready within300seconds; source was not started')
                time.sleep(0.5)
            save(out/'worker-ready.json',{'checked_utc':datetime.now(timezone.utc).isoformat(),
                 'wait_seconds':time.monotonic()-ready_started,'actual_progress':json.loads((out/'progress.json').read_text()),
                 'source_container_not_started':subprocess.run(['docker','inspect',flink_name],capture_output=True).returncode!=0})
            source=runner.scenario('corrected')
            originals=[r['event'] for phase in fixture_phases() for r in phase if r['kind']=='telemetry' and r['event']['event_id']!='E']
            compare_windows(source['windows'],window_truth(originals))
            if not source['runtime']['passed'] or source['late_event_ids']!=['E'] or source['durable_ledger_records']!=6:
                raise ValueError('Real Kafka/Flink fixture differs from known replay/late-event truth')
            save(out/'source-report.json',source)
            save(out/'SOURCE_DONE.json',{'source_job_finished':True,'source_id':source_id,'flink_job_id':source['runtime']['job_id'],
                 'expected_window_rows':len(source['windows']),'windows_semantic_sha256':semantic_digest(source['windows']),
                 'source_report_sha256':digest(out/'source-report.json')})
            if worker.wait(timeout=120)!=0:
                raise RuntimeError('Native live push worker failed; inspect its retained console and journal')
        stopped.set();observer.join(timeout=5)
        if guard or not live or not live[0]['post_job_export_not_yet_present']:
            raise ValueError('No acknowledged push was observed while the real Flink source job was still running')
        result=json.loads((out/'report.json').read_text())
        if (not result['passed'] or result['offline_rows']!=3 or result['historical_rows']!=3
                or json.loads((out/'_STREAM_PUSH_SUCCESS.json').read_text())['report_sha256']!=digest(out/'report.json')
                or any(digest(ROOT/n)!=h for n,h in fence.items())):
            raise ValueError('Native push result/source/active-ingest binding differs')
        proof={'passed':True,'preflight_sha256':digest(out/'preflight.json'),'worker_report_sha256':digest(out/'report.json'),
               'worker_ready_sha256':digest(out/'worker-ready.json'),
               'source_report_sha256':digest(out/'source-report.json'),'observed_live_push_sha256':digest(out/'observed-live-push.json'),
               'source_done_sha256':digest(out/'SOURCE_DONE.json'),'source_window_rows':3,'source_reading_count_total':sum(r['reading_count'] for r in source['windows']),
               'too_late_event_ids':source['late_event_ids'],'full_ingest_code_unchanged':True,
               'scope':'Real single-partition Kafka/Flink final windows pushed before source completion, then exact native Feast offline/history/online checks. Not a transactional checkpoint sink, model rolling features or100GB streaming.'}
        save(out/'verification.json',proof);print(json.dumps(proof),flush=True)
    finally:
        stopped.set()
        if worker is not None and worker.poll() is None:
            subprocess.run(['docker','stop','-t','15',worker_name],capture_output=True);worker.wait(timeout=45)
        if started_kafka:subprocess.run(runner.base+['stop','kafka'],capture_output=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True)
    parser.add_argument('--deadline-utc',required=True)
    parser.add_argument('--native-proof',type=Path,required=True)
    parser.add_argument('--ingest-fence',type=Path)
    run(parser.parse_args())
