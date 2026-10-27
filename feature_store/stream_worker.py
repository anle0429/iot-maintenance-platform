"""Tail committed finalized Flink SQLite windows and push verified unique features to native Feast stores."""

import argparse
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import time

from feature_store.common import digest,save,utc_now
from feature_store.outbox import window_payload
from feature_store.push import WindowPusher


def source_windows(database: Path):
    """Use read-only SQL; SQLite may coordinate shared WAL metadata in the writable sink directory."""
    if not database.is_file():
        return []
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)) as connection:
        try:
            values=connection.execute('SELECT payload FROM windows ORDER BY window_start,machine_id').fetchall()
        except sqlite3.OperationalError as error:
            if 'no such table: windows' in str(error):
                return []
            raise
    return [json.loads(value[0]) for value in values]


def semantic_digest(rows):
    """Fingerprint absolute finalized values independently of read order and transport creation time."""
    values=sorted((window_payload(row)[0],window_payload(row)[1]) for row in rows)
    return sha256(json.dumps(values,separators=(',',':')).encode()).hexdigest()


def run(args):
    """Declare success only after the source job is finished and every window is in both verified stores."""
    if args.report.exists() or args.report.with_name('_STREAM_PUSH_SUCCESS.json').exists():
        raise ValueError('Choose a fresh worker report; retain prior results')
    if not 1<=args.max_seconds<=1800:
        raise ValueError('Bound the polling worker to at most1800seconds')
    started=time.monotonic();events=[];observations=0
    progress=args.report.with_name('progress.json')
    with WindowPusher(args.state,args.source_id) as writer:
        while True:
            rows=source_windows(args.database);observations+=1
            for row in rows:
                mode=writer.push(row)
                if mode!='completed_replay_noop':
                    event={'machine_id':row['machine_id'],'event_timestamp':row['event_timestamp'],'mode':mode,
                           'source_created_wall_utc':row['created'],'pushed_wall_utc':utc_now()}
                    events.append(event);print(json.dumps(event),flush=True)
            completed=sum(x['completed_utc'] is not None for x in writer.outbox.records())
            save(progress,{'updated_utc':utc_now(),'source_window_rows':len(rows),'completed_unique_windows':completed,
                           'native_push_events':len(events),'poll_observations':observations,'seconds':time.monotonic()-started})
            if args.source_done.is_file():
                receipt=json.loads(args.source_done.read_text())
                if (not receipt['source_job_finished'] or receipt['source_id']!=args.source_id
                        or receipt['expected_window_rows']!=len(rows) or receipt['windows_semantic_sha256']!=semantic_digest(rows)):
                    raise ValueError('Finished source window receipt differs from the committed live sink')
                proof=writer.verify()
                if proof['offline_rows']!=len(rows):
                    raise ValueError('Pushed window history differs from the complete Flink result')
                report={**proof,'source_id':args.source_id,'source_database':str(args.database),
                        'source_done_sha256':digest(args.source_done),'source_windows_semantic_sha256':semantic_digest(rows),
                        'push_events':events,'poll_observations':observations,'seconds':time.monotonic()-started,
                        'worker_code_sha256':digest(Path(__file__)),'completed_utc':utc_now(),
                        'clock_policy':'event_timestamp is simulated window end; created is actual Flink finalization audit time. Historical equality uses created filterFalse and does not claim availability at simulated window end.',
                        'scope':'A bounded polling push job consuming committed final Flink outputs, with native Feast offline/online/historical readback. Not a transactional Flink checkpoint sink, model rolling features or continuous production deployment.'}
                save(args.report,report)
                save(args.report.with_name('_STREAM_PUSH_SUCCESS.json'),{'report_sha256':digest(args.report)})
                print({'passed':True,'offline_rows':proof['offline_rows'],'native_push_events':len(events)},flush=True)
                return
            if time.monotonic()-started>args.max_seconds:
                raise TimeoutError('Only the bounded stream push worker timed out; retain its journal and reports')
            time.sleep(0.5)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('database','state','report','source-done'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--source-id',required=True)
    parser.add_argument('--max-seconds',type=int,default=600)
    run(parser.parse_args())
