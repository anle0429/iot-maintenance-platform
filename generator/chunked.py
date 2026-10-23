"""Generate continuous pump histories into bounded JSONL parts without materializing the dataset."""

import argparse
from datetime import timedelta
from hashlib import sha256
from fractions import Fraction
import heapq
import json
import math
from pathlib import Path
import shutil
import sys

from generator.config import Config, iso_ts
from generator.simulation import machine_records, rng_for
from generator.storage import write_json


FORMAT_VERSION = 'chunked-jsonl-1'
TABLES = {'machines':'machines','maintenance':'maintenance_events','failures':'failure_events',
          'v1':'telemetry/schema_v1','v2':'telemetry/schema_v2','stream':'stream'}


class ChunkConfig(Config):
    """Reuse physical validation while giving the separate streaming writer its own resource guards."""

    row_limit = None

    def __post_init__(self):
        """Keep the legacy 100k guard intact and cap the number of simultaneously active pumps."""
        super().__post_init__()
        if self.machine_count > 10_000:
            raise ValueError('chunked generator supports at most 10,000 active machine states')
        self.end


def encoded(row: dict) -> bytes:
    """Serialize one canonical record so chunk boundaries cannot change logical bytes."""
    return (json.dumps(row,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()


def digest_file(path: Path) -> str:
    """Hash a file with a fixed read buffer, including files larger than available RAM."""
    digest=sha256()
    with path.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def existing_parent(path: Path) -> Path:
    """Find an existing filesystem ancestor before creating an output directory."""
    path=path.resolve()
    while not path.exists():
        path=path.parent
    return path


def anomaly_count(count: int, rate: float) -> int:
    """Floor the configured decimal rate exactly, avoiding cases such as 180*0.7 becoming 125.9999."""
    ratio=Fraction(str(rate))
    return count*ratio.numerator//ratio.denominator


class ExactSelector:
    """Select exactly floor(N*rate) positions uniformly without storing an N-element ID set."""

    def __init__(self, count: int, rate: float, rng):
        """Retain only how many positions and selections remain across the entire run."""
        self.remaining=count
        self.needed=anomaly_count(count,rate)
        self.rng=rng

    def take(self) -> bool:
        """Select the next position with probability needed/remaining, without per-chunk rounding."""
        if not self.remaining:
            raise ValueError('selector exhausted')
        chosen=self.needed>0 and (self.needed==self.remaining or self.rng.randrange(self.remaining)<self.needed)
        self.remaining-=1
        self.needed-=chosen
        return chosen


class DiskBudget:
    """Enforce a hard byte cap and recheck reserved free disk at part boundaries."""

    def __init__(self, root: Path, maximum: int, reserve: int):
        """Track actual output bytes, independent of estimated bytes in the capacity plan."""
        if type(maximum) is not int or maximum<1 or type(reserve) is not int or reserve<0:
            raise ValueError('maximum bytes must be positive; reserve bytes must be nonnegative')
        self.root=root
        self.maximum=maximum
        self.reserve=reserve
        self.written=0

    def check_disk(self):
        """Check the real output filesystem before opening each new part."""
        if shutil.disk_usage(existing_parent(self.root)).free <= self.reserve:
            raise OSError('free disk reached the configured reserve; incomplete run retained')

    def charge(self, amount: int):
        """Reject the next write before crossing the hard output cap."""
        if self.written+amount>self.maximum:
            raise OSError('maximum output bytes exceeded; incomplete run retained')
        self.written+=amount


class PartWriter:
    """Write one table incrementally and rotate files without buffering a chunk of Python rows."""

    def __init__(self, root: Path, table: str, chunk_rows: int, budget: DiskBudget, catalog):
        """Retain fixed-size IO buffers and per-table counters/hash state only."""
        self.root,self.table,self.limit,self.budget,self.catalog=root,table,chunk_rows,budget,catalog
        self.handle=None
        self.parts=0
        self.rows=0
        self.bytes=0
        self.logical=sha256()

    def write(self, row: dict):
        """Append one serialized row and close a part at the configured stored-row boundary."""
        if self.handle is None:
            self.budget.check_disk()
            self.path=self.root/TABLES[self.table]/f'part-{self.parts:06d}.jsonl'
            self.path.parent.mkdir(parents=True,exist_ok=True)
            self.handle=self.path.open('xb',buffering=1024*1024)
            self.file_hash=sha256()
            self.part_rows=self.part_bytes=0
        line=encoded(row)
        self.budget.charge(len(line))
        self.handle.write(line)
        self.file_hash.update(line)
        self.logical.update(line)
        self.part_rows+=1
        self.part_bytes+=len(line)
        self.rows+=1
        self.bytes+=len(line)
        if self.part_rows==self.limit:
            self.close()

    def close(self):
        """Close a part and append its measured metadata to an on-disk catalog."""
        if self.handle is None:
            return
        self.handle.close()
        self.handle=None
        item={'table':self.table,'part':self.parts,'path':self.path.relative_to(self.root).as_posix(),
              'rows':self.part_rows,'bytes':self.part_bytes,'sha256':self.file_hash.hexdigest()}
        line=encoded(item)
        self.budget.charge(len(line))
        self.catalog.write(line)
        self.parts+=1

    def summary(self) -> dict:
        """Return constant-size totals and a hash of concatenated logical table bytes."""
        return {'rows':self.rows,'bytes':self.bytes,'parts':self.parts,'logical_sha256':self.logical.hexdigest()}


def generate_chunked(config: ChunkConfig, root: Path, chunk_rows: int = 100_000,
                     include_stream: bool = True, maximum_bytes: int = 2*1024**3,
                     reserve_bytes: int = 1024**3, max_buffered_deliveries: int = 250_000) -> dict:
    """Generate one continuous history with bounded machine/arrival state and explicit completion markers."""
    if type(chunk_rows) is not int or not 1<=chunk_rows<=1_000_000:
        raise ValueError('chunk_rows must be between 1 and 1,000,000')
    if type(max_buffered_deliveries) is not int or max_buffered_deliveries<1:
        raise ValueError('max_buffered_deliveries must be positive')
    if root.exists():
        raise FileExistsError(f'source exists: {root}')
    budget=DiskBudget(root,maximum_bytes,reserve_bytes)
    budget.check_disk()
    delay=max(3,config.late_max_seconds if config.late_rate else 3)
    retry=config.retry_delay_seconds if config.stream_duplicate_rate else 0
    pending_bound=min(config.unique_rows+anomaly_count(config.unique_rows,config.stream_duplicate_rate),
                      2*config.machine_count*(math.ceil((delay+retry)/config.sample_seconds)+2))
    if include_stream and pending_bound>max_buffered_deliveries:
        raise ValueError('arrival buffer bound exceeds max_buffered_deliveries; use offline-only or a smaller shape')
    root.mkdir(parents=True,exist_ok=False)
    selectors={name:ExactSelector(config.unique_rows,rate,rng_for(config,'chunked-1-'+name)) for name,rate in
               [('late',config.late_rate),('offline',config.offline_duplicate_rate),('stream',config.stream_duplicate_rate)]}
    delay_rng=rng_for(config,'chunked-1-delay')
    pending=[]
    peak_pending=0
    with (root/'files.jsonl').open('xb') as catalog:
        writers={name:PartWriter(root,name,chunk_rows,budget,catalog) for name in TABLES}
        try:
            pumps=[machine_records(config,number) for number in range(config.machine_count)]
            for pump in pumps:
                kind,row=next(pump)
                if kind!='machines':
                    raise ValueError('physical iterator must start with machine metadata')
                writers[kind].write(row)
            for step in range(config.steps):
                now=config.start+timedelta(seconds=step*config.sample_seconds)
                for pump in pumps:
                    for kind,row in pump:
                        if kind=='telemetry':
                            break
                        writers[kind].write(row)
                    else:
                        raise ValueError('physical iterator ended before the complete time grid')
                    late=selectors['late'].take()
                    duplicate=selectors['offline'].take()
                    retry_selected=selectors['stream'].take()
                    received=now+timedelta(seconds=delay_rng.randint(config.late_min_seconds,config.late_max_seconds) if late else delay_rng.randint(0,3))
                    row={**row,'received_ts':iso_ts(received),'created_ts':iso_ts(received)}
                    writer=writers[f"v{row['schema_version']}"]
                    writer.write(row)
                    if duplicate:
                        writer.write({**row,'created_ts':iso_ts(received+timedelta(seconds=config.export_delay_seconds))})
                    if include_stream:
                        for attempt in range(1,3 if retry_selected else 2):
                            delivery={'delivery_id':f"{row['event_id']}-{attempt}",'attempt':attempt,
                                      'published_ts':iso_ts(received+timedelta(seconds=(attempt-1)*config.retry_delay_seconds)),'event':row}
                            heapq.heappush(pending,(delivery['published_ts'],delivery['delivery_id'],delivery))
                peak_pending=max(peak_pending,len(pending))
                if len(pending)>max_buffered_deliveries:
                    raise ValueError('actual arrival buffer exceeded its configured bound')
                while pending and pending[0][0]<=iso_ts(now):
                    writers['stream'].write(heapq.heappop(pending)[2])
            while pending:
                writers['stream'].write(heapq.heappop(pending)[2])
        finally:
            for writer in writers.values():
                writer.close()
    tables={name:writer.summary() for name,writer in writers.items()}
    manifest={'format_version':FORMAT_VERSION,'physical_simulation_version':'1.0.0','transport_selection':'sequential-exact-1',
              'format':'JSONL','compression':'none','config':config.to_dict(),'chunk_rows':chunk_rows,
              'stream_generated':include_stream,'peak_pending_deliveries':peak_pending,
              'max_buffered_deliveries':max_buffered_deliveries,'tables':tables,
              'payload_bytes':sum(t['bytes'] for t in tables.values()),
              'offline_payload_bytes':sum(t['bytes'] for name,t in tables.items() if name!='stream'),
              'catalog':{'path':'files.jsonl','bytes':(root/'files.jsonl').stat().st_size,'sha256':digest_file(root/'files.jsonl')},
              'scope':'Continuous history; state O(machines + bounded lateness), no resumable/checkpointed generation.'}
    for name,value in [('manifest.json',manifest),('_GENERATION_SUCCESS.json',{'manifest_sha256':None})]:
        if name.startswith('_'):
            value['manifest_sha256']=digest_file(root/'manifest.json')
        text=(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
        budget.charge(len(text))
        (root/name).write_bytes(text)
    return manifest


def main() -> int:
    """Expose separate chunked commands while preserving the legacy capped generator CLI."""
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    create=commands.add_parser('generate')
    create.add_argument('--config',type=Path,default=Path('generator/configs/chunked-pilot.json'))
    create.add_argument('--output',type=Path,required=True)
    create.add_argument('--chunk-rows',type=int,default=100_000)
    create.add_argument('--offline-only',action='store_true')
    create.add_argument('--maximum-bytes',type=int,default=2*1024**3)
    create.add_argument('--reserve-bytes',type=int,default=1024**3)
    create.add_argument('--max-buffered-deliveries',type=int,default=250_000)
    for name in ('inspect','plan','compare'):
        command=commands.add_parser(name)
        if name=='compare':
            command.add_argument('--left',type=Path,required=True)
            command.add_argument('--right',type=Path,required=True)
            command.add_argument('--logical-only',action='store_true')
        else:
            command.add_argument('--source',type=Path,required=True)
        command.add_argument('--report',type=Path,required=True)
        if name=='plan':
            command.add_argument('--target-offline-gb',type=float,default=100)
            command.add_argument('--offline-only',action='store_true')
    args=parser.parse_args()
    try:
        if args.command=='generate':
            manifest=generate_chunked(ChunkConfig.load(args.config),args.output,args.chunk_rows,not args.offline_only,
                                      args.maximum_bytes,args.reserve_bytes,args.max_buffered_deliveries)
            print(f"Generated {manifest['offline_payload_bytes']:,} actual offline bytes; {args.output}")
        else:
            sources=[args.left,args.right] if args.command=='compare' else [args.source]
            for source in sources:
                if source.resolve()==args.report.resolve() or source.resolve() in args.report.resolve().parents:
                    raise ValueError('report must be outside the immutable source')
            from generator.chunk_quality import inspect_chunked
            if args.command=='compare':
                left,right=inspect_chunked(args.left),inspect_chunked(args.right)
                logical=left['config']==right['config'] and all(left['tables'][name]['logical_sha256']==right['tables'][name]['logical_sha256'] for name in TABLES)
                identical=digest_file(args.left/'manifest.json')==digest_file(args.right/'manifest.json')
                report={'passed':logical and (args.logical_only or identical),'logical_tables_identical':logical,
                        'all_source_bytes_identical':identical,'both_sources_fully_validated':True,
                        'comparison':'logical' if args.logical_only else 'byte-identical'}
            else:
                report=inspect_chunked(args.source)
            if args.command=='plan':
                from generator.capacity import capacity_plan
                report=capacity_plan(report,args.source,args.target_offline_gb,args.offline_only)
            write_json(args.report,report)
            print(f"{'PASS' if report['passed'] else 'FAIL'}: {args.command}; report={args.report}")
            if not report['passed']:
                return 1
        return 0
    except (ValueError,TypeError,KeyError,OSError,OverflowError) as error:
        print(f'ERROR: {error}',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
