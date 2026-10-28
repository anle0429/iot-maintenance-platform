"""Exercise separated native offline/online writes across process failures and replay, using retained finalized Flink input."""
from pathlib import Path
import argparse,json,subprocess,sys
from feature_store.common import digest,save,utc_now
from feature_store.staging_contract import fixture
from feature_store.staged_push import OfflineStage,OnlineStage


def run(args):
    """Retain every native phase and refuse to overwrite an existing evidence file."""
    if args.output.exists():raise ValueError('Preserve the previous stage report')
    rows=fixture(Path(__file__).resolve().parents[1])['windows'];modes=[];checks={};source_id=args.source_id
    if args.operation=='sequence':
        args.state.mkdir(parents=True,exist_ok=False);reports={}
        for operation in ['offline-fault','offline-finish','online-fault','online-finish']:
            target=args.state/(operation+'.json');command=[sys.executable,'-m','feature_store.staged_checks','--operation',operation,
                '--state',str(args.state/'offline'),'--online-state',str(args.state/'online'),'--source-id',source_id,
                '--offline-report',str(args.state/'offline-finish.json'),'--output',str(target)]
            with (args.state/(operation+'.txt')).open('x') as f:result=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,timeout=240)
            expected=3 if operation.endswith('-fault') else 0
            if result.returncode!=expected:raise RuntimeError('Unexpected native stage exit: '+operation)
            reports[operation]=json.loads(target.read_bytes())
            if reports[operation].get('passed') is not True:raise ValueError('Native stage report failed')
        save(args.output,{'passed':True,'source_id':source_id,'reports':reports,'scope':'Fresh real native Feast writes; offline writes no online features, online writes no offline rows; independent process failures/replay. Retained genuine Kafka/Flink source, no fresh live source claim.'});return
    offline=args.operation.startswith('offline')
    context=OfflineStage(args.state,source_id) if offline else OnlineStage(args.online_state,args.state,json.loads(args.offline_report.read_bytes()))
    with context as writer:
        if args.operation.endswith('-fault'):
            row=rows[0] if offline else max(rows,key=lambda v:v['event_timestamp'])
            expected='Injected after '+('offline' if offline else 'online')+' write before acknowledgement'
            try:writer.push(row,'after_offline' if offline else 'after_online')
            except RuntimeError as error:
                if str(error)!=expected:raise
            else:raise ValueError('Native write fault did not occur')
            checks['one_pending_intent']=sum(v['completed_utc'] is None for v in writer.outbox.records())==1
            if offline:
                checks['one_real_offline_row']=len(writer.offline_frame())==1
                checks['online_still_missing']=writer.online_values([row['machine_id']])[['reading_count','temperature_sum','temperature_mean','vibration_max']].isna().all().all().item()
            else:
                values=writer.store.get_online_features(features=writer.service,entity_rows=[{'machine_id':row['machine_id']}]).to_df()
                checks['real_latest_online_write']=float(values.iloc[0].temperature_mean)==40.0
                checks['offline_unchanged']=digest(writer.offline)==writer.receipt['offline_sha256']
            report={'passed':all(checks.values()),'operation':args.operation,'checks':checks,'source_id':source_id,'observed_utc':utc_now()}
        else:
            ordered=rows if offline else sorted(rows,key=lambda v:v['event_timestamp'],reverse=True)
            modes=[writer.push(row) for row in ordered];before=digest(writer.offline)
            replay=[writer.push(row) for row in reversed(ordered)]
            checks['replay_all_noop']=all(v==('completed_offline_replay_noop' if offline else 'completed_online_replay_noop') for v in replay)
            checks['replay_offline_bytes_unchanged']=before==digest(writer.offline)
            try:writer.push({**rows[0],'vibration_max':999.0})
            except ValueError:checks['conflicting_source_rejected']=True
            else:checks['conflicting_source_rejected']=False
            proof=writer.verify();checks['three_acknowledged_windows']=(proof['offline_rows'] if offline else proof['acknowledged_windows'])==3
            checks['two_machines']=proof['machines']==2
            if offline:checks['offline_did_not_populate_online']=proof['online_all_missing']
            else:checks['latest_values_40_and_100']=[r['temperature_mean'] for r in proof['online_rows']]==[40.0,100.0]
            report={**proof,'passed':proof['passed'] and all(checks.values()),'operation':args.operation,'modes':modes,'replay_modes':replay,'checks':checks}
        save(args.output,report)
        if report['passed'] is not True:raise ValueError('Native staged write checks failed')
    if args.operation.endswith('-fault'):raise SystemExit(3)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--operation',choices=['sequence','offline-fault','offline-finish','online-fault','online-finish'],required=True)
    for name in ['state','output']:p.add_argument('--'+name,type=Path,required=True)
    for name in ['online-state','offline-report']:p.add_argument('--'+name,type=Path)
    p.add_argument('--source-id',required=True);run(p.parse_args())
