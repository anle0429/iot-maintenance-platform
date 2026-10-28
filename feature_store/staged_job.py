"""Run one separately deployed stream-store job against exact retained finalized windows, with native replay verification."""
from pathlib import Path
import argparse,json
from feature_store.common import digest,save
from feature_store.staging_contract import fixture
from feature_store.staged_push import OfflineStage,OnlineStage


def run(args):
    """Write only the selected destination and fail on changed input, replay effects or native vector differences."""
    if args.output.exists():raise ValueError('Preserve the previous job report')
    source=fixture(Path(__file__).resolve().parents[1]);rows=source['windows']
    if args.destination=='offline':context=OfflineStage(args.state,args.source_id)
    else:
        report=json.loads(args.offline_report.read_bytes())
        context=OnlineStage(args.online_state,args.state,report)
        rows=sorted(rows,key=lambda v:v['event_timestamp'],reverse=True)
    with context as writer:
        before=digest(writer.offline);modes=[writer.push(row) for row in rows];proof=writer.verify()
        if args.operation=='replay':
            expected='completed_'+args.destination+'_replay_noop'
            if modes!=[expected]*3 or digest(writer.offline)!=before:raise ValueError('Completed replay changed a store')
        if args.destination=='offline' and not proof['online_all_missing']:raise ValueError('This fresh offline CI release populated online values')
        if args.destination=='online' and [r['temperature_mean'] for r in proof['online_rows']]!=[40.0,100.0]:raise ValueError('Latest monitoring vectors differ')
        save(args.output,{**proof,'source_id':args.source_id,'operation':args.operation,'modes':modes,'fixture_parent_sha256':source['parent_sha256'],
            'scope':'Separately deployed native '+args.destination+' job; three exact retained Kafka/Flink windows, not a fresh live source run or model rolling features.'})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--destination',choices=['offline','online'],required=True);p.add_argument('--operation',choices=['write','replay'],required=True)
    for name in ['state','output']:p.add_argument('--'+name,type=Path,required=True)
    for name in ['online-state','offline-report']:p.add_argument('--'+name,type=Path)
    p.add_argument('--source-id',required=True);run(p.parse_args())
