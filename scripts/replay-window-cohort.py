"""Replay a measured prefix or the complete retained5760-window mini-coursework cohort into separate native Feast jobs."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime,timezone
import argparse,json,math,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from feature_store.outbox import window_payload,FIELDS


def load_windows(parent,expected_sha256,rows):
    """Require the exact successful full Kafka/Flink report, unique valid windows and an explicit bounded prefix."""
    if not parent.is_file() or parent.stat().st_size>8*1024**2:raise ValueError('Require a bounded retained stream report')
    raw=parent.read_bytes()
    if sha256(raw).hexdigest()!=expected_sha256:raise ValueError('Retained stream report hash differs')
    source=json.loads(raw);generated=source['generated'];windows=generated['windows']
    if (source.get('passed') is not True or generated['runtime'].get('passed') is not True or len(windows)!=5760
            or generated['durable_ledger_records']!=57600 or generated['kafka_end_offset']!=116928 or sum(r['reading_count'] for r in windows)!=57600):raise ValueError('Require the verified complete5760-window/57600-reading source')
    if type(rows) is not int or not 1<=rows<=5760:raise ValueError('Select1..5760 source windows')
    identities=[window_payload(r) for r in windows]
    if len({key for key,_,_ in identities})!=5760:raise ValueError('Retained finalized source contains duplicate keys')
    selected=sorted(windows,key=lambda r:(r['event_timestamp'],r['machine_id']))[:rows]
    binding={'parent_sha256':expected_sha256,'full_windows':5760,'full_readings':57600,'selected_windows':rows,'selected_machines':len({r['machine_id'] for r in selected}),'selected_readings':sum(r['reading_count'] for r in selected),'selected_semantic_sha256':sha256(json.dumps(sorted((window_payload(r)[0],window_payload(r)[1]) for r in selected),separators=(',',':')).encode()).hexdigest(),'scope':'Retained real Kafka/Flink outputs from22OctPC; native store replay now, not a fresh live source or model rolling features.'}
    return selected,binding


def run(args):
    """Use independently acknowledged store stages; require replay no-ops and native cohort equality before success."""
    from feature_store.staged_push import OfflineStage,OnlineStage
    from feature_store.common import digest,save
    if args.output.exists():raise ValueError('Choose a fresh result file')
    rows,binding=load_windows(args.parent,args.parent_sha256,args.rows);started=time.monotonic()
    if args.destination=='offline':context=OfflineStage(args.state,args.source_id)
    else:
        upstream=json.loads(args.offline_report.read_bytes())
        if upstream['source_binding']!=binding or upstream['source_id']!=args.source_id:raise ValueError('Online source differs from the completed offline cohort')
        context=OnlineStage(args.online_state,args.state,upstream);rows=list(reversed(rows))
    own_state=args.state if args.destination=='offline' else args.online_state;bound=own_state/'cohort-source.json';identity={'source_id':args.source_id,'destination':args.destination,'binding':binding}
    if bound.exists():
        if json.loads(bound.read_bytes())!=identity:raise ValueError('Existing cohort source binding differs')
    elif (own_state/'stage-binding.json').exists():raise ValueError('Do not adopt an existing unbound stage')
    with context as writer:
        if not bound.exists():
            with bound.open('x') as stream:stream.write(json.dumps(identity,indent=2)+'\n')
        before=digest(writer.offline);modes=[];setup_seconds=time.monotonic()-started;push_started=time.monotonic()
        for index,row in enumerate(rows,1):
            modes.append(writer.push(row))
            if index%128==0:print(json.dumps({'destination':args.destination,'operation':args.operation,'processed':index,'elapsed_seconds':time.monotonic()-started}),flush=True)
        push_seconds=time.monotonic()-push_started;verify_started=time.monotonic();proof=writer.verify();verify_seconds=time.monotonic()-verify_started
        if args.destination=='offline' and (proof['offline_rows']!=len(rows) or proof['historical_rows']!=len(rows)):raise ValueError('Offline cohort count differs')
        if args.destination=='online' and (proof['acknowledged_windows']!=len(rows) or proof['machines']!=binding['selected_machines']):raise ValueError('Online cohort count differs')
        if args.operation=='replay' and (modes!=['completed_'+args.destination+'_replay_noop']*len(rows) or digest(writer.offline)!=before):raise ValueError('Replay changed the acknowledged store')
        if args.destination=='offline':
            if not proof['online_all_missing']:raise ValueError('Offline stage populated the fresh online project')
            # Native verification is exact; preserve PythonFloat64 values instead of pandas.to_json's default10digits.
            proof['rows']=writer.offline_frame().to_dict(orient='records')
        if args.destination=='online':
            expected={}
            for row in rows:
                _,_,p=window_payload(row)
                if p['machine_id'] not in expected or p['event_timestamp']>expected[p['machine_id']]['event_timestamp']:expected[p['machine_id']]=p
            native=writer.store.get_online_features(features=writer.service,entity_rows=[{'machine_id':m} for m in sorted(expected)]).to_df()
            actual={r['machine_id']:r for r in native.to_dict(orient='records')}
            if actual.keys()!=expected.keys() or any(any(actual[m][f]!=expected[m][f] for f in FIELDS) for m in expected):raise ValueError('Latest native vectors differ from the retained source oracle')
            proof['online_rows']=[actual[m] for m in sorted(actual)];proof['expected_latest_rows']=[expected[m] for m in sorted(expected)]
        save(args.output,{**proof,'source_binding':binding,'operation':args.operation,'mode_counts':{m:modes.count(m) for m in sorted(set(modes))},'setup_seconds':setup_seconds,'push_seconds':push_seconds,'verify_seconds':verify_seconds,'elapsed_seconds':time.monotonic()-started,'completed_utc':datetime.now(timezone.utc).isoformat()})
    print(json.dumps({'passed':True,'destination':args.destination,'operation':args.operation,'windows':len(rows),'elapsed_seconds':time.monotonic()-started}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--parent',type=Path,required=True);p.add_argument('--parent-sha256',required=True);p.add_argument('--rows',type=int,required=True);p.add_argument('--destination',choices=['offline','online'],required=True);p.add_argument('--operation',choices=['write','replay'],required=True);p.add_argument('--source-id',required=True)
    for name in ['state','output']:p.add_argument('--'+name,type=Path,required=True)
    for name in ['online-state','offline-report']:p.add_argument('--'+name,type=Path)
    run(p.parse_args())
