"""Export exact two-column snapshot labels and join them to fresh, label-free native Feast lookups."""
import argparse,fcntl,json,time
from datetime import datetime,timedelta,timezone
from pathlib import Path

from feature_store.batch import compare_features
from feature_store.common import create_store,utc_microseconds
from feature_store.repository import SERVICE
from ml.protocol import PROTOCOL_SHA256,load_protocol
from ml.snapshot import snapshot_id
from ml.training_contract import digest,inventory,save


def run(args):
    """Keep labels out of Feast's entity frame and verify a one-to-one merge for every eligible row."""
    protocol=load_protocol(args.protocol);started=time.monotonic()
    batch=json.loads(args.batch_report.read_text());features=json.loads(args.feature_report.read_text())
    compaction=json.loads((args.batch_report.parent/'compaction.json').read_text())
    if (not batch['passed'] or not features['passed'] or batch['protocol_sha256']!=PROTOCOL_SHA256
        or features['protocol_sha256']!=PROTOCOL_SHA256
        or json.loads((args.state/'_BATCH_READY.json').read_text())['report_sha256']!=digest(args.batch_report)
        or digest(args.state/'machine_rolling.parquet')!=batch['compact_sha256']
        or batch['compaction_sha256']!=digest(args.batch_report.parent/'compaction.json')
        or compaction['feature_report_sha256']!=digest(args.feature_report)
        or json.loads((args.artifacts/'_FEATURES_SUCCESS.json').read_text())['report_sha256']!=digest(args.feature_report)):
        raise ValueError('Frozen protocol/source/registry binding differs')
    entity_parts=[p for p in features['files'] if p['path'].startswith('training/eligible_entities/')]
    entity_dir=args.artifacts/'training/eligible_entities'
    if {x['path'] for x in entity_parts}!={p.relative_to(args.artifacts).as_posix() for p in entity_dir.glob('*.parquet')}:
        raise ValueError('Entity inventory changed')
    for item in entity_parts:
        if digest(args.artifacts/item['path'])!=item['sha256']:raise ValueError('Label source bytes changed')
    if args.output.exists() or args.workspace.exists():raise ValueError('Use a fresh notebook dataset workspace')
    args.workspace.mkdir(parents=True);parts=args.workspace/'feast-cohort';parts.mkdir()
    fields=protocol['feature_allowlist'];days=[];counts={}
    with (args.state.parent/'write.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
        store,backend=create_store('iot_maintenance',args.state/'registry');service=store.get_feature_service(SERVICE)
        try:
            day=datetime.fromisoformat(protocol['chronological_split']['start_inclusive'].replace('Z','+00:00'))
            end=datetime.fromisoformat(protocol['chronological_split']['observation_end_exclusive'].replace('Z','+00:00'))
            while day<end:
                tomorrow=min(day+timedelta(days=1),end)
                source=backend.con.execute('SELECT * FROM read_parquet(?) WHERE event_timestamp>=? AND event_timestamp<? ORDER BY machine_id,event_timestamp',
                    [str(entity_dir/'*.parquet'),day.replace(tzinfo=None),tomorrow.replace(tzinfo=None)]).fetchdf()
                source=utc_microseconds(source,['event_timestamp','label_available_ts'])
                source['id']=[snapshot_id(m,t.to_pydatetime()) for m,t in zip(source.machine_id,source.event_timestamp)]
                if source['id'].duplicated().any() or not source.failure_in_next_1h.isin([0,1]).all():
                    raise ValueError('Unknown label or duplicate prediction snapshot')
                labels=source[['id','failure_in_next_1h']].rename(columns={'failure_in_next_1h':'label'})
                key_columns=['id','machine_id','event_timestamp','label_available_ts','failure_episode_id','split']
                keys=source[key_columns]
                # Independently compute the canonical SHA key inside DuckDB, rather than trusting a shared helper.
                backend.con.register('snapshot_keys',keys)
                if backend.con.execute("SELECT count(*) FROM snapshot_keys WHERE id<>sha256(machine_id||'|'||strftime(event_timestamp AT TIME ZONE 'UTC','%Y-%m-%dT%H:%M:%S.%fZ'))").fetchone()[0]:
                    raise ValueError('Independent SQL snapshot identity differs')
                day_name=day.date().isoformat()
                label_path=args.workspace/'labels'/('day='+day_name)/'labels.parquet'
                key_path=args.workspace/'snapshot-keys'/('day='+day_name)/'keys.parquet'
                for path,frame in [(label_path,labels),(key_path,keys)]:
                    path.parent.mkdir(parents=True,exist_ok=False)
                    frame.to_parquet(path,index=False,compression='snappy',coerce_timestamps='us')
                # Exactly two keys: no label, split, episode or future metadata enters Feast's lookup.
                queries=source[['machine_id','event_timestamp']].copy()
                loaded=store.get_historical_features(entity_df=queries,features=service,filter_by_created_timestamp=False).to_df()
                if set(loaded.columns)!={'machine_id','event_timestamp',*fields}:raise ValueError('Unexpected Feast feature schema')
                loaded=utc_microseconds(loaded,['event_timestamp'])
                backend.con.register('expected_entities',queries)
                expected=backend.con.execute('SELECT e.machine_id,e.event_timestamp,'+','.join('f.'+n for n in fields)
                    +' FROM expected_entities e JOIN read_parquet(?) f USING(machine_id,event_timestamp)',[str(args.state/'machine_rolling.parquet')]).fetchdf()
                compare_features(loaded,expected,fields)
                merged=loaded.merge(keys,on=['machine_id','event_timestamp'],validate='one_to_one').merge(labels,on='id',validate='one_to_one')
                if len(merged)!=len(source) or merged.label.isna().any():raise ValueError('Label merge lost/duplicated a snapshot')
                audit=merged[['id','label']].merge(source[['id','failure_in_next_1h']],on='id',validate='one_to_one')
                if not (audit.label==audit.failure_in_next_1h).all():raise ValueError('Label join changed truth')
                output_frame=merged.rename(columns={'label':'failure_in_next_1h'})
                destination=parts/('day='+day_name)/'data.parquet';destination.parent.mkdir()
                output_frame.to_parquet(destination,index=False,compression='snappy',coerce_timestamps='us')
                for name,group in output_frame.groupby('split'):
                    count=counts.setdefault(name,{'split':name,'rows':0,'positive_rows':0})
                    count['rows']+=len(group);count['positive_rows']+=int(group.failure_in_next_1h.sum())
                days.append({'day':day_name,'rows':len(merged),'label_columns':list(labels.columns),'label_sha256':digest(label_path),
                    'keys_sha256':digest(key_path),'merged_sha256':digest(destination)})
                if len(days)==1:
                    sample=merged[['id','machine_id','event_timestamp','label',*fields]].head(3).copy()
                    sample['event_timestamp']=sample.event_timestamp.astype(str)
                    save(args.workspace/'merge-sample.json',sample.to_dict(orient='records'))
                print({'day':day_name,'native_feast_rows':len(loaded),'label_merge_rows':len(merged)},flush=True)
                day=tomorrow
            total=sum(x['rows'] for x in days)
            if total!=features['cohort_exclusion_counts']['included'] or total!=3_132_184:raise ValueError('Incomplete native cohort')
            for name,count in counts.items():
                if any(count[k]!=features['splits'][name][k] for k in ['rows','positive_rows']):raise ValueError('Split support changed')
            for item in entity_parts:
                if digest(args.artifacts/item['path'])!=item['sha256']:raise ValueError('Label source changed while loading')
            if digest(args.state/'machine_rolling.parquet')!=batch['compact_sha256']:raise ValueError('Feature source changed while loading')
            report=args.workspace/'load-report.json'
            save(report,{'passed':True,'protocol_sha256':PROTOCOL_SHA256,'total_rows':total,'native_rows_this_invocation':total,
                'labels_schema':['id','label'],'snapshot_identity':'SHA256(machine_id|UTC timestamp with six fractional digits)',
                'feast_entity_columns':['machine_id','event_timestamp'],'split_counts':list(counts.values()),'days':days,
                'source_feature_report_sha256':digest(args.feature_report),'batch_report_sha256':digest(args.batch_report),
                'code_sha256':{p.name:digest(p) for p in [Path(__file__),Path(__file__).with_name('snapshot.py')]},
                'seconds':time.monotonic()-started,'completed_utc':datetime.now(timezone.utc).isoformat()})
            save(args.output,{'passed':True,'kind':'feast-cohort','protocol_sha256':PROTOCOL_SHA256,'path':str(parts),
                'report_path':str(report),'report_sha256':digest(report),'split_counts':list(counts.values()),
                'source_manifest_sha256':protocol['source']['manifest_sha256'],
                'artifacts':[{'path':str(args.workspace/n),'files':inventory(args.workspace/n)} for n in ['feast-cohort','labels','snapshot-keys']]})
        finally:backend.disconnect()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['workspace','output','protocol','state','artifacts','batch-report','feature-report']:
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--executor-input');args=parser.parse_args();run(args)
    if args.executor_input:
        from ml.kfp_artifacts import publish
        publish(args.executor_input,'cohort',args.output)
