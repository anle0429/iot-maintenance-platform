"""Validate ordered chunked sources with bounded reads, complete grids and a bounded arrival-time join."""

from collections import Counter
from datetime import timedelta
from hashlib import sha256
import heapq
import json
import math
import os
from pathlib import Path

from generator.chunked import ChunkConfig, FORMAT_VERSION, TABLES, anomaly_count, digest_file
from generator.config import iso_ts, parse_ts
from generator.quality import NUMERIC_FIELDS, TELEMETRY_V1
from generator.simulation import physical_identity


def require(condition: bool, message: str):
    """Fail closed on the first violated invariant instead of emitting a successful report."""
    if not condition:
        raise ValueError(message)


def records(path: Path):
    """Read one bounded JSON object at a time, rejecting oversized or unterminated records."""
    with path.open('rb') as source:
        while line:=source.readline(16_385):
            require(len(line)<=16_384 and line.endswith(b'\n'),f'oversized/unterminated row: {path.name}')
            value=json.loads(line)
            require(isinstance(value,dict),'records must be JSON objects')
            yield value


def table_rows(root: Path, manifest: dict, table: str):
    """Follow a table's contiguous part numbers without retaining the full file catalog."""
    for number in range(manifest['tables'][table]['parts']):
        yield from records(root/TABLES[table]/f'part-{number:06d}.jsonl')


def walk_files(directory: Path):
    """Walk file entries lazily and reject symlinks rather than following data outside the run."""
    with os.scandir(directory) as entries:
        for entry in entries:
            require(not entry.is_symlink(),'source may not contain symlinks')
            if entry.is_dir(follow_symlinks=False):
                yield from walk_files(Path(entry.path))
            else:
                require(entry.is_file(follow_symlinks=False),'source contains a non-regular file')
                yield Path(entry.path)


def verify_files(root: Path, manifest: dict) -> dict:
    """Check every catalog/file hash, part bound, logical hash and unlisted file with fixed buffers."""
    require(manifest['format_version']==FORMAT_VERSION,'unsupported format version')
    require(manifest['format']=='JSONL' and manifest['compression']=='none','unsupported storage encoding')
    require(set(manifest['tables'])==set(TABLES),'table catalog differs')
    require(type(manifest['chunk_rows']) is int and 1<=manifest['chunk_rows']<=1_000_000,'invalid chunk bound')
    catalog=root/'files.jsonl'
    require(manifest['catalog']=={'path':'files.jsonl','bytes':catalog.stat().st_size,'sha256':digest_file(catalog)},'file catalog integrity failed')
    summary={name:{'parts':0,'rows':0,'bytes':0} for name in TABLES}
    hashes={name:sha256() for name in TABLES}
    for item in records(catalog):
        table=item['table']
        require(table in TABLES,'unknown table in catalog')
        current=summary[table]
        expected=f"{TABLES[table]}/part-{current['parts']:06d}.jsonl"
        require(item['part']==current['parts'] and item['path']==expected,'catalog part sequence/path differs')
        path=root/expected
        require(path.resolve().is_relative_to(root.resolve()) and not path.is_symlink(),'unsafe source path')
        digest=sha256()
        size=rows=0
        with path.open('rb') as source:
            for block in iter(lambda:source.read(1024*1024),b''):
                digest.update(block)
                hashes[table].update(block)
                size+=len(block)
                rows+=block.count(b'\n')
        require(size==item['bytes'] and rows==item['rows'] and digest.hexdigest()==item['sha256'],'part integrity failed')
        require(0<rows<=manifest['chunk_rows'],'part exceeds chunk row bound')
        current['parts']+=1
        current['rows']+=rows
        current['bytes']+=size
    for name,stats in summary.items():
        stats['logical_sha256']=hashes[name].hexdigest()
        require(stats==manifest['tables'][name],f'table summary mismatch: {name}')
    actual_count=total_bytes=0
    for path in walk_files(root):
        relative=path.relative_to(root).as_posix()
        if relative not in ('manifest.json','files.jsonl','_GENERATION_SUCCESS.json'):
            matched=False
            for name,prefix in TABLES.items():
                if relative.startswith(prefix+'/part-') and relative.endswith('.jsonl'):
                    number=relative[len(prefix+'/part-'):-6]
                    matched=number.isdigit() and relative==f'{prefix}/part-{int(number):06d}.jsonl' and int(number)<summary[name]['parts']
                    break
            require(matched,f'unlisted source file: {relative}')
        actual_count+=1
        total_bytes+=path.stat().st_size
    require(actual_count==sum(v['parts'] for v in summary.values())+3,'file inventory mismatch')
    require(sum(v['bytes'] for v in summary.values())==manifest['payload_bytes'],'payload byte count differs')
    offline=sum(v['bytes'] for k,v in summary.items() if k!='stream')
    require(offline==manifest['offline_payload_bytes'],'offline byte count differs')
    return {'offline_payload_bytes':offline,'payload_bytes':manifest['payload_bytes'],
            'source_total_bytes':total_bytes,'source_files':actual_count,'tables':summary}


def machine_map(root: Path, manifest: dict, config: ChunkConfig) -> dict:
    """Validate every machine key and snapshot while retaining only O(machine_count) factories."""
    factories={}
    hot=math.floor(config.machine_count*config.hot_factory_share+0.5)
    fields={'machine_id','factory_id','machine_type','rated_rpm','installed_at','valid_from_ts','valid_to_ts','is_current'}
    for number,row in enumerate(table_rows(root,manifest,'machines')):
        key=f'P-{number+1:03d}'
        expected_factory='F-01' if number<hot else f'F-{2+(number-hot)%2:02d}'
        require(set(row)==fields and row['machine_id']==key,'machine key/schema differs')
        require(row['factory_id']==expected_factory and row['machine_type']=='centrifugal_pump' and row['rated_rpm']==3000,'machine metadata differs')
        require(row['valid_from_ts']==iso_ts(config.start) and row['valid_to_ts'] is None and row['is_current'] is True,'machine SCD snapshot differs')
        require(row['installed_at']==iso_ts(config.start-timedelta(days=180+number*7)),'installation timestamp differs')
        factories[key]=row['factory_id']
    require(len(factories)==config.machine_count,'machine count differs')
    return factories


def stop_events(root: Path, manifest: dict, config: ChunkConfig, factories: dict, table: str):
    """Read sorted repair/maintenance intervals, checking keys, censoring, bounds and recorded times."""
    failed=table=='failures'
    fields=({'failure_id','machine_id','failure_start_ts','recovered_ts','failure_type','recorded_ts'} if failed else
            {'maintenance_id','machine_id','start_ts','end_ts','maintenance_type','component','created_ts'})
    previous=None
    for row in table_rows(root,manifest,table):
        require(set(row)==fields and row['machine_id'] in factories,'stop event schema/FK differs')
        start=parse_ts(row['failure_start_ts' if failed else 'start_ts'])
        step=(start-config.start).total_seconds()/config.sample_seconds
        require(config.start<=start<config.end and step.is_integer(),'stop event outside observed time grid')
        key=(start,int(row['machine_id'][2:]))
        require(previous is None or previous<key,'stop events unordered or duplicated')
        previous=key
        identifier=f"{'F' if failed else 'M'}-{physical_identity(config)}-{row['machine_id']}-{int(step):06d}"
        require(row['failure_id' if failed else 'maintenance_id']==identifier,'stop event identity differs')
        end=start+timedelta(minutes=config.repair_minutes if failed else config.maintenance_minutes)
        require(row['recovered_ts' if failed else 'end_ts']==(iso_ts(end) if end<config.end else None),'stop censoring/end differs')
        require(row['recorded_ts' if failed else 'created_ts']==iso_ts(start+timedelta(seconds=30 if failed else 0)),'stop availability differs')
        require(row['failure_type']=='bearing_wear' if failed else row['maintenance_type']=='preventive' and row['component']=='bearing','stop type differs')
        yield start,row['machine_id'],min(end,config.end),'failed' if failed else 'maintenance'


def telemetry_rows(root: Path, manifest: dict):
    """Concatenate firmware partitions in event-time order, preserving duplicate adjacency across parts."""
    for version in (1,2):
        for row in table_rows(root,manifest,f'v{version}'):
            require(row.get('schema_version')==version,'wrong firmware file partition')
            yield row


def originals(root: Path, manifest: dict):
    """Re-read only original rows after their grid and adjacent duplicates have been validated."""
    previous=None
    for row in telemetry_rows(root,manifest):
        if row['event_id']!=previous:
            yield row
        previous=row['event_id']


def verify_telemetry(root: Path, manifest: dict, config: ChunkConfig, factories: dict) -> dict:
    """Check a complete known grid and physical downtime with O(machines) interval state."""
    events=heapq.merge(stop_events(root,manifest,config,factories,'failures'),
                       stop_events(root,manifest,config,factories,'maintenance'),key=lambda v:(v[0],int(v[1][2:])))
    upcoming=next(events,None)
    active={}
    previous=None
    duplicated=False
    count=extras=late=0
    minimum=maximum=None
    statuses,factories_seen,schemas=Counter(),Counter(),Counter()
    identity=physical_identity(config)
    for row in telemetry_rows(root,manifest):
        if previous is not None and row['event_id']==previous['event_id']:
            require(not duplicated,'more than one offline extra for one event')
            require(row=={**previous,'created_ts':iso_ts(parse_ts(previous['received_ts'])+timedelta(seconds=config.export_delay_seconds))},'duplicate payload/export timestamp differs')
            duplicated=True
            extras+=1
            continue
        step,number=divmod(count,config.machine_count)
        require(step<config.steps,'extra unique reading beyond observation')
        machine=f'P-{number+1:03d}'
        now=config.start+timedelta(seconds=step*config.sample_seconds)
        version=2 if step>=math.floor(config.steps*config.schema_v2_fraction) else 1
        require(row['event_id']==f'E-{identity}-{machine}-{step:06d}' and row['machine_id']==machine and row['event_ts']==iso_ts(now),'reading missing, duplicated, reordered or has wrong key/grid')
        require(set(row)==TELEMETRY_V1|({'pressure_bar'} if version==2 else set()),'telemetry fields differ or expose latent truth')
        require(type(row['schema_version']) is int and row['schema_version']==version,'schema transition differs')
        require(all(type(row[f]) in (int,float) and math.isfinite(row[f]) and row[f]>=0 for f in NUMERIC_FIELDS+(('pressure_bar',) if version==2 else ())), 'invalid sensor value/type')
        received=parse_ts(row['received_ts'])
        delay=(received-now).total_seconds()
        require(delay.is_integer() and (0<=delay<=3 or config.late_min_seconds<=delay<=config.late_max_seconds),'arrival delay outside configured ranges')
        require(row['created_ts']==row['received_ts'],'original export must equal first arrival')
        if delay>=config.late_min_seconds:
            late+=1
            minimum=delay if minimum is None else min(minimum,delay)
            maximum=delay if maximum is None else max(maximum,delay)
        while upcoming is not None and upcoming[0]<=now:
            start,key,end,status=upcoming
            require(key not in active or active[key][0]<=start,'stop intervals overlap')
            active[key]=(end,status)
            upcoming=next(events,None)
        interval=active.get(machine)
        expected=interval[1] if interval and now<interval[0] else 'operating'
        require(row['operating_status']==expected,'operating status disagrees with stop truth')
        require(row['rpm']>0 and row['current_a']>0 if expected=='operating' else row['rpm']==row['current_a']==0 and (version==1 or row['pressure_bar']==0),'motor values disagree with stop status')
        statuses[expected]+=1
        factories_seen[factories[machine]]+=1
        schemas[str(version)]+=1
        previous=row
        duplicated=False
        count+=1
    require(count==config.unique_rows and upcoming is None,'incomplete readings or unmatched stop truth')
    require(extras==anomaly_count(count,config.offline_duplicate_rate),'global offline duplicate count differs')
    require(late==anomaly_count(count,config.late_rate),'global late count differs')
    hot=math.floor(config.machine_count*config.hot_factory_share+0.5)
    require(factories_seen['F-01']==hot*config.steps,'factory skew differs')
    return {'unique_telemetry_rows':count,'offline_duplicate_extras':extras,'late_unique_events':late,
            'late_delay_seconds_min':minimum,'late_delay_seconds_max':maximum,
            'factory_unique_counts':dict(factories_seen),'schema_unique_counts':dict(schemas),
            'operating_status_unique_counts':dict(statuses)}


def verify_stream(root: Path, manifest: dict, config: ChunkConfig) -> dict:
    """Join arrival-ordered deliveries to offline truth without a whole-dataset ID map or sort."""
    if not manifest['stream_generated']:
        require(manifest['tables']['stream']['rows']==0,'offline-only source contains stream rows')
        return {'generated':False,'scope':'not generated; stream checks not applicable'}
    source=originals(root,manifest)
    upcoming=next(source,None)
    pending={}
    deadlines=[]
    previous=None
    previous_event=None
    count=retries=inversions=peak=0

    def retire(before=None):
        """Retire an event only after its latest possible retry and require its primary delivery."""
        while deadlines and (before is None or deadlines[0][0]<before):
            _,key=heapq.heappop(deadlines)
            _,seen=pending.pop(key)
            require(seen&1,'offline reading missing from stream')

    for delivery in table_rows(root,manifest,'stream'):
        require(set(delivery)=={'delivery_id','attempt','published_ts','event'},'stream envelope fields differ')
        published=parse_ts(delivery['published_ts'])
        order=(delivery['published_ts'],delivery['delivery_id'])
        require(previous is None or previous<order,'stream not globally arrival-ordered or duplicate delivery key')
        previous=order
        while upcoming is not None and upcoming['event_ts']<=delivery['published_ts']:
            key=upcoming['event_id']
            pending[key]=[upcoming,0]
            retry=config.retry_delay_seconds if config.stream_duplicate_rate else 0
            heapq.heappush(deadlines,(parse_ts(upcoming['received_ts'])+timedelta(seconds=retry),key))
            require(len(pending)<=manifest['max_buffered_deliveries'],'validation arrival join exceeded memory bound')
            upcoming=next(source,None)
        retire(published)
        event=delivery['event']
        require(isinstance(event,dict) and event.get('event_id') in pending,'stream event absent from offline grid/arrival horizon')
        original,seen=pending[event['event_id']]
        attempt=delivery['attempt']
        require(type(attempt) is int and attempt in (1,2),'invalid delivery attempt')
        bit=1<<(attempt-1)
        require(not seen&bit and (attempt==1 or seen&1),'repeated attempt or retry without primary')
        require(event==original and delivery['delivery_id']==f"{event['event_id']}-{attempt}",'stream payload/identity differs from offline original')
        require(published==parse_ts(event['received_ts'])+timedelta(seconds=(attempt-1)*config.retry_delay_seconds),'retry timestamp differs')
        pending[event['event_id']][1]|=bit
        retries+=attempt==2
        count+=1
        peak=max(peak,len(pending))
        inversions+=previous_event is not None and previous_event>event['event_ts']
        previous_event=event['event_ts']
    retire()
    require(upcoming is None,'stream ended before all offline events could arrive')
    expected_retries=anomaly_count(config.unique_rows,config.stream_duplicate_rate)
    require(count==config.unique_rows+expected_retries and retries==expected_retries,'global retry/delivery counts differ')
    return {'generated':True,'deliveries':count,'duplicate_extras':retries,'event_time_inversions':inversions,'peak_join_events':peak}


def inspect_chunked(root: Path) -> dict:
    """Read all persisted bytes and semantic records before issuing a successful quality report."""
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    marker=json.loads((root/'_GENERATION_SUCCESS.json').read_text(encoding='utf-8'))
    require(marker=={'manifest_sha256':digest_file(root/'manifest.json')},'generation incomplete or manifest changed')
    config=ChunkConfig(**manifest['config'])
    files=verify_files(root,manifest)
    factories=machine_map(root,manifest,config)
    telemetry=verify_telemetry(root,manifest,config,factories)
    stream=verify_stream(root,manifest,config)
    checks={name:True for name in ('completion_and_manifest','catalog_and_all_part_checksums','no_unlisted_files',
            'stored_row_chunk_bound','table_logical_hashes','actual_byte_counts','machine_keys_and_snapshot',
            'complete_unique_time_grid','schema_and_sensor_types','global_offline_duplicate_count_and_payload',
            'global_late_count_and_delay','factory_skew','stop_keys_intervals_censoring_availability','motor_status_matches_stops')}
    if stream['generated']:
        checks.update({name:True for name in ('stream_global_arrival_order','stream_payload_matches_offline',
                       'stream_primary_and_retry_identity','global_stream_duplicate_count','bounded_stream_join')})
    return {'passed':True,'checks':checks,'format_version':FORMAT_VERSION,'format':'JSONL','compression':'none',
            'config':config.to_dict(),'chunk_rows':manifest['chunk_rows'],**files,**telemetry,'stream':stream,
            'failure_events':manifest['tables']['failures']['rows'],'maintenance_events':manifest['tables']['maintenance']['rows'],
            'generation_peak_pending_deliveries':manifest['peak_pending_deliveries'],
            'scope':'Full readback of this source, using its ordered-grid contract; no whole-dataset sets, no 100GB claim from estimates.'}
