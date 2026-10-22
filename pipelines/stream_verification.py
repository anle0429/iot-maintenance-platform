"""Publish ordered Kafka workloads and verify real Flink windows, late side output and replay results."""

import argparse
from collections import Counter
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request

from generator.storage import write_json
from pipelines.source_loader import validate_run_id
from pipelines.stream_contract import fixture_phases, millis, read_store, window_truth


class StreamRunner:
    """Run isolated, finite scenarios through one actual Kafka partition and Flink job."""

    def __init__(self, docker: str, run_id: str, ui_pause_seconds: int = 0):
        """Preserve original input, console output and commands in a new run directory."""
        self.docker = docker
        self.run_id = validate_run_id(run_id)
        self.output = Path('outputs')/run_id
        self.data = Path('data/stream')/run_id
        if self.output.exists() or self.data.exists():
            raise ValueError('stream run exists; choose a new run name')
        self.output.mkdir(parents=True)
        self.data.mkdir(parents=True)
        self.base = [docker,'compose','-f','infra/streaming.compose.yaml']
        self.commands = []
        self.ui_pause_seconds = ui_pause_seconds

    def command(self, arguments: list[str], payload: bytes | None = None) -> str:
        """Execute one checked Kafka CLI call without exposing ports or adding host dependencies."""
        result = subprocess.run(self.base + arguments, input=payload, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
        self.commands.append({'arguments':arguments,'exit_code':result.returncode,'output':result.stdout.decode(errors='replace')})
        write_json(self.output/'commands.json',{'commands':self.commands})
        return result.stdout.decode(errors='replace')

    def kafka(self, tool: str, arguments: list[str], payload: bytes | None = None) -> str:
        """Give each short-lived Kafka administration/producer process a bounded JVM heap."""
        return self.command(['exec','-T','-e','KAFKA_HEAP_OPTS=-Xms32m -Xmx128m','kafka',f'/opt/kafka/bin/{tool}'] + arguments,payload)

    def publish(self, topic: str, rows: list[dict], filename: str) -> None:
        """Keep source arrival order within a pass; replay rounds are explicitly marked."""
        payload = ''.join(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n' for row in rows).encode()
        (self.output/filename).write_bytes(payload)
        self.kafka('kafka-console-producer.sh',['--bootstrap-server','kafka:9092','--topic',topic,
                   '--producer-property','acks=all','--producer-property','enable.idempotence=true'],payload)

    def capture_rest(self, scenario: str) -> None:
        """Record actual Flink REST plan/state/checkpoint responses while the workload waits."""
        try:
            with urllib.request.urlopen('http://127.0.0.1:18081/jobs/overview',timeout=5) as response:
                overview = json.load(response)
            write_json(self.output/f'{scenario}-rest-overview.json',overview)
            for job in overview['jobs']:
                for endpoint in ('','/checkpoints','/plan'):
                    with urllib.request.urlopen(f"http://127.0.0.1:18081/jobs/{job['jid']}{endpoint}",timeout=5) as response:
                        content = json.load(response)
                    write_json(self.output/f"{scenario}-rest-{endpoint.strip('/') or 'details'}.json",content)
        except (OSError,KeyError) as error:
            write_json(self.output/f'{scenario}-rest-unavailable.json',{'error':str(error)})

    def barrier(self, process, database: Path, marker: int, minimum_watermark: int) -> None:
        """Wait for a committed audit containing a real Flink watermark; fail instead of assuming progress."""
        deadline = time.monotonic()+120
        while time.monotonic()<deadline:
            if process.poll() is not None:
                raise RuntimeError('Flink exited before the watermark barrier; inspect scenario console')
            progress = database.with_name(f'{database.stem}-progress-{marker}.json')
            if progress.exists():
                try:
                    row = json.loads(progress.read_text())
                    if row['kind']=='progress' and row['marker']==marker and row['watermark_ms']>=minimum_watermark:
                        return
                except (OSError,json.JSONDecodeError):
                    pass
            time.sleep(0.25)
        raise TimeoutError(f'Flink marker {marker} did not reach watermark {minimum_watermark}')

    def scenario(self, name: str, baseline: bool = False, deliveries: list[dict] | None = None, ttl_probe: bool = False) -> dict:
        """Run either the controlled late/replay fixture or two full generator stream passes."""
        topic = f'iot-{self.run_id}-{name}'.lower()
        self.kafka('kafka-topics.sh',['--bootstrap-server','kafka:9092','--create','--topic',topic,'--partitions','1','--replication-factor','1'])
        phases = fixture_phases() if deliveries is None else [deliveries]
        if ttl_probe:
            ordinary = fixture_phases()
            first = ordinary[0][0]
            phases = [[first,{**first,'sequence':100},ordinary[0][-1]],
                      [{**first,'sequence':101},ordinary[1][-1]],[ordinary[2][-1]]]
        records = sum(map(len,phases))
        database = self.data/f'{name}.sqlite'
        container_name = f'iot-stream-{self.run_id}-{name}'.lower()
        arguments = ['run','--rm','--service-ports','--name',container_name,'flink','-m','pipelines.stream_job',
                     '--topic',topic,'--records',str(records),'--database',database.as_posix(),
                     '--runtime-report',(self.output/f'{name}-runtime.json').as_posix()]
        if baseline:
            arguments.append('--baseline')
        if ttl_probe:
            arguments += ['--dedup-retention-ms','1500']
        if deliveries is not None:
            arguments += ['--checkpoint-interval-ms','30000']
        self.commands.append({'arguments':arguments,'status':'started'})
        write_json(self.output/'commands.json',{'commands':self.commands})
        with (self.output/f'{name}-console.txt').open('wb') as log:
            process = subprocess.Popen(self.base+arguments,stdout=log,stderr=subprocess.STDOUT)
            started = time.monotonic()
            try:
                for number,phase in enumerate(phases,start=1):
                    self.publish(topic,phase,f'{name}-phase-{number}.jsonl')
                    if deliveries is None and number<3:
                        marker = phase[-1]
                        expected = millis(marker['event_ts']) - (0 if baseline else 15*60000) - 1
                        self.barrier(process,database,number,expected)
                        if ttl_probe and number==1:
                            expiry = database.with_name(f"{database.stem}-expired-{sha256(b'A').hexdigest()[:12]}.json")
                            deadline=time.monotonic()+30
                            while not expiry.exists():
                                if process.poll() is not None or time.monotonic()>deadline:
                                    raise TimeoutError('Actual processing-time expiry was not observed')
                                time.sleep(0.1)
                        if number==2:
                            self.capture_rest(name)
                            if self.ui_pause_seconds and not ttl_probe:
                                print(f'UI capture ready: {name}; http://127.0.0.1:18081; waiting {self.ui_pause_seconds}s',flush=True)
                                time.sleep(self.ui_pause_seconds)
                while process.poll() is None:
                    if time.monotonic()-started>600:
                        raise TimeoutError('Bounded Flink job exceeded ten minutes')
                    time.sleep(0.5)
                if process.returncode:
                    raise RuntimeError(f'{name} Flink exit={process.returncode}; inspect console')
            except BaseException:
                subprocess.run([self.docker,'stop','--timeout','10',container_name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                process.wait(timeout=30)
                raise
        offset = self.kafka('kafka-get-offsets.sh',['--bootstrap-server','kafka:9092','--topic',topic])
        if int(offset.strip().split(':')[-1]) != records:
            raise ValueError('Kafka end offset differs from published record count')
        content = read_store(database)
        runtime = json.loads((self.output/f'{name}-runtime.json').read_text())
        ledger_count=0
        if not baseline:
            from contextlib import closing
            snapshot=database.with_name(database.stem+'-dedup.sqlite').resolve()
            if not snapshot.is_file() or snapshot.stat().st_size==0:
                raise ValueError('Completed job did not export its event ledger snapshot')
            with closing(sqlite3.connect(f'{snapshot.as_uri()}?mode=ro',uri=True)) as connection:
                ledger_count=connection.execute('SELECT count(*) FROM events').fetchone()[0]
        return {'runtime':runtime,'topic':topic,'kafka_end_offset':records,
                'durable_ledger_records':ledger_count,
                'duplicates_from_durable_ledger':sum(row['kind']=='duplicate' and row.get('dedup_source')=='durable_ledger' for row in content['audits']),
                'audit_counts':dict(Counter(row['kind'] for row in content['audits'])),
                'windows':content['windows'],
                'late_event_ids':sorted(row['event']['event_id'] for row in content['audits'] if row['kind']=='late'),
                'progress':[row for row in content['audits'] if row['kind']=='progress']}


def compare_windows(actual: list[dict], expected: list[dict]) -> None:
    """Compare keys/counts and numeric totals with an independent Python oracle."""
    if len(actual)!=len(expected):
        raise ValueError(f'Window count differs: {len(actual)} != {len(expected)}')
    for left,right in zip(actual,expected):
        for field,value in right.items():
            if isinstance(value,float):
                if not math.isclose(left[field],value,rel_tol=1e-9,abs_tol=1e-7):
                    raise ValueError(f'Window numeric mismatch: {field}')
            elif left[field]!=value:
                raise ValueError(f'Window key/count mismatch: {field}')


def main() -> None:
    """Publish real workloads, assert known answers and save a report only on complete success."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--docker',default='docker')
    parser.add_argument('--source',type=Path,default=Path('data/source/day1'))
    parser.add_argument('--fixture-only',action='store_true')
    parser.add_argument('--ui-pause-seconds',type=int,choices=range(61),default=0)
    args = parser.parse_args()
    runner = StreamRunner(args.docker,args.run_id,args.ui_pause_seconds)
    baseline = runner.scenario('baseline',baseline=True)
    print('PASS: baseline finished; validating corrected workload',flush=True)
    corrected = runner.scenario('corrected')
    events = [row['event'] for phase in fixture_phases() for row in phase if row['kind']=='telemetry' and row['event']['event_id']!='E']
    compare_windows(corrected['windows'],window_truth(events))
    if corrected['audit_counts'].get('unique')!=6 or corrected['audit_counts'].get('duplicate')!=13 or corrected['late_event_ids']!=['E']:
        raise ValueError('Corrected fixture audit counts or late policy differ from known answers')
    if baseline['windows'][0]['reading_count']!=6 or not math.isclose(baseline['windows'][0]['temperature_mean'],50/3):
        raise ValueError('Baseline did not expose expected duplicate inflation')
    if 'D' not in baseline['late_event_ids'] or 'D' in corrected['late_event_ids']:
        raise ValueError('Watermark delay did not recover the eight-minute late reading')
    report = {'passed':True,'base_commit':subprocess.check_output(['git','rev-parse','HEAD']).decode().strip(),
              'checks':{'known_answer_windows':True,'half_open_boundary':True,'machine_partition':True,
                        'retry_and_replay_deduplicated':True,'within_watermark_late_recovered':True,
                        'beyond_watermark_quarantined':True,'real_watermark_barriers':True,'kafka_offsets_match':True},
              'baseline':baseline,'corrected':corrected}
    print('PASS: known-answer fixture, dedup and late policy',flush=True)
    ttl_probe = runner.scenario('retention-probe',ttl_probe=True)
    if len(ttl_probe['windows'])!=1 or ttl_probe['windows'][0]['reading_count']!=1 or ttl_probe['audit_counts'].get('duplicate')!=2 or ttl_probe['audit_counts'].get('expired',0)<1 or ttl_probe['duplicates_from_durable_ledger']!=1:
        raise ValueError('Short retention probe did not preserve idempotency after expiry')
    report['retention_probe']=ttl_probe
    report['checks']['processing_time_retention_expiry_observed']=True
    print('PASS: retention expiry observed; durable ledger prevented replay double counting',flush=True)
    if not args.fixture_only:
        originals = [json.loads(line) for line in (args.source/'stream/telemetry_deliveries.jsonl').read_text().splitlines()]
        deliveries = [{**row,'kind':'telemetry','replay_round':number,'sequence':number*len(originals)+index}
                      for number in (1,2) for index,row in enumerate(originals)]
        generated = runner.scenario('generated',deliveries=deliveries)
        expected = window_truth([row['event'] for row in originals])
        compare_windows(generated['windows'],expected)
        if generated['audit_counts'].get('unique')!=57600 or generated['audit_counts'].get('duplicate')!=len(deliveries)-57600 or generated['late_event_ids'] or generated['durable_ledger_records']!=57600:
            raise ValueError('Generator replay counts differ from actual source truth')
        report['generated'] = {**generated,'source_sha256':sha256((args.source/'stream/telemetry_deliveries.jsonl').read_bytes()).hexdigest()}
        report['checks']['generator_full_replay_windows_match_source_truth'] = True
        print('PASS: generator replay verified against independent window truth',flush=True)
    report['code_sha256'] = [{'path':p.as_posix(),'sha256':sha256(p.read_bytes()).hexdigest()}
                             for folder in ('pipelines','tests','infra') for p in sorted(Path(folder).rglob('*'))
                             if p.is_file() and (p.suffix in ('.py','.yaml') or p.name=='Dockerfile')]
    write_json(runner.output/'stream-report.json',report)
    write_json(runner.data/'_RUN_SUCCESS.json',{'passed':True,'report':(runner.output/'stream-report.json').as_posix()})
    print(f'PASS: streaming milestone; report={runner.output}/stream-report.json',flush=True)


if __name__ == '__main__':
    main()
