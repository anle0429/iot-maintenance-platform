"""Prepare a separate native Feast online namespace and arrival-aware operating context for an explicit simulation-clock demo."""
from pathlib import Path
from datetime import datetime,timedelta
from collections import Counter
import argparse,json
from google.protobuf.json_format import MessageToDict
from feature_store.common import create_store,digest,save,utc_now
from feature_store.repository import register_batch,SERVICE,VIEW
from feature_store.batch import online_matches
from ml.protocol import load_protocol,PROTOCOL_SHA256

PROJECT='iot_serving_demo'


def run(args):
    """Never refresh another pipeline's checkpoint or interpret simulated future events as current wall time."""
    protocol=load_protocol(args.protocol);cutoff=datetime.fromisoformat(args.cutoff.replace('Z','+00:00'))
    start=datetime.fromisoformat(protocol['chronological_split']['start_inclusive'].replace('Z','+00:00'))
    end=datetime.fromisoformat(protocol['chronological_split']['observation_end_exclusive'].replace('Z','+00:00'))
    if cutoff.tzinfo is None or not start+timedelta(hours=1)<=cutoff<end:raise ValueError('Choose an aware cutoff inside the sealed source horizon')
    batch=json.loads(args.batch_report.read_text());compact=args.source_state/'machine_rolling.parquet'
    binding=json.loads((args.batch_report.parent/'compaction.json').read_text())
    if not batch['passed'] or batch['protocol_sha256']!=PROTOCOL_SHA256 or digest(compact)!=batch['compact_sha256'] or digest(args.batch_report.parent/'compaction.json')!=batch['compaction_sha256']:
        raise ValueError('Native source/compaction binding differs')
    inputs=json.loads(args.input_binding.read_text())
    if inputs['source_manifest_sha256']!=protocol['source']['manifest_sha256']:raise ValueError('Operating source differs from model protocol')
    for item in inputs['input_files']:
        if item['path'].startswith(('silver/stg_telemetry/','bronze/raw_machines/')) and digest(args.silver/item['path'])!=item['sha256']:raise ValueError('Operating context source bytes changed')
    args.state.mkdir(parents=True,exist_ok=False)
    store,backend=create_store(PROJECT,args.state/'registry')
    try:
        service,_=register_batch(store,compact,binding,args.protocol)
        store.materialize(start_date=cutoff-timedelta(hours=1),end_date=cutoff,feature_views=[VIEW])
        comparison=online_matches(store,backend,service,compact,protocol['feature_allowlist'],cutoff)
        naive=cutoff.replace(tzinfo=None)
        frame=backend.con.execute("SELECT machine_id,operating_status,event_ts,received_ts,event_id FROM read_parquet(?) WHERE event_ts<=? AND received_ts<=? QUALIFY row_number() OVER(PARTITION BY machine_id ORDER BY event_ts DESC,received_ts DESC,event_id DESC)=1 ORDER BY machine_id",[str(args.silver/'silver/stg_telemetry/*.parquet'),naive,naive]).fetchdf()
        if len(frame)!=protocol['source']['machine_count'] or not set(frame.operating_status)<=set(['operating','failed','maintenance']):raise ValueError('Operating context has unknown/missing machines')
        rows={}
        for row in frame.to_dict(orient='records'):
            event=row['event_ts'].to_pydatetime().replace(tzinfo=cutoff.tzinfo);received=row['received_ts'].to_pydatetime().replace(tzinfo=cutoff.tzinfo)
            if not 0<=(cutoff-event).total_seconds()<=600 or received>cutoff:raise ValueError('Operating context is stale or unavailable at the demo clock')
            rows[row['machine_id']]={'status':row['operating_status'],'event_ts':event.isoformat(),'received_ts':received.isoformat(),'event_id':row['event_id']}
        context={'clock_mode':'simulation','as_of':cutoff.isoformat(),'prepared_wall_utc':utc_now(),'protocol_sha256':PROTOCOL_SHA256,'source_manifest_sha256':inputs['source_manifest_sha256'],'input_binding_sha256':digest(args.input_binding),'machines':rows,'scope':'Fixed logical-clock demonstration; not current wall-clock machine state. Only event/received<=as_of; no future label or failure table used.'}
        save(args.state/'context.json',context)
        sample=store.get_online_features(features=service,entity_rows=[{'machine_id':frame.machine_id.iloc[0]}])
        save(args.state/'native-response.json',MessageToDict(sample.proto,preserving_proto_field_name=True))
        report={'passed':True,'project':PROJECT,'cutoff':cutoff,'context_sha256':digest(args.state/'context.json'),'compact_sha256':digest(compact),'online_comparison':comparison,'context_status_counts':dict(Counter(frame.operating_status)),'code_sha256':digest(Path(__file__)),'completed_utc':utc_now(),'scope':context['scope']+' Separate Redis project with1hwallTTL; old materialization/training registries untouched.'}
        save(args.state/'verification.json',report);save(args.state/'_READY.json',{'verification_sha256':digest(args.state/'verification.json'),'context_sha256':digest(args.state/'context.json')})
    finally:backend.disconnect()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for n in ['state','source-state','batch-report','protocol','silver','input-binding']:parser.add_argument('--'+n,type=Path,required=True)
    parser.add_argument('--cutoff',required=True);run(parser.parse_args())
