"""Read all native Feast monitoring values, statuses and per-field event timestamps without writing either store."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime,timezone
import argparse,json,os


def inspect(state,offline_report,online_report,output):
    """Require the sealed upstream registry and every latest value/timestamp through the real native SDK response."""
    from feast import FeatureStore
    from feast.repo_config import RepoConfig
    from google.protobuf.json_format import MessageToDict
    if output.exists():raise ValueError('Preserve the previous inspection')
    offline=json.loads(offline_report.read_bytes());online=json.loads(online_report.read_bytes());registry=state/'registry/registry.db';before=sha256(registry.read_bytes()).hexdigest()
    if not offline['passed'] or not online['passed'] or offline['source_binding']!=online['source_binding'] or before!=offline['registry_sha256'] or offline['project']!=online['project']:raise ValueError('Require the completed bound offline and online cohort')
    expected={r['machine_id']:r for r in online['expected_latest_rows']};ids=sorted(expected)
    store=FeatureStore(config=RepoConfig(project=online['project'],provider='local',registry=str(registry),offline_store={'type':'duckdb'},online_store={'type':'redis','connection_string':os.environ['IOT_FEAST_REDIS'],'key_ttl_seconds':3600}))
    service=store.get_feature_service('machine_window_monitoring');response=store.get_online_features(features=service,entity_rows=[{'machine_id':m} for m in ids]);raw=response.to_dict(include_event_timestamps=True);native=MessageToDict(response.proto,preserving_proto_field_name=True)
    vectors=dict(zip(native['metadata']['feature_names']['val'],native['results'],strict=True));fields=['reading_count','temperature_sum','temperature_mean','vibration_max'];assert raw['machine_id']==ids
    for field in fields:
        assert vectors[field]['statuses']==['PRESENT']*len(ids)
        for index,machine in enumerate(ids):assert raw[field][index]==expected[machine][field] and datetime.fromtimestamp(raw[field+'__ts'][index],timezone.utc)==datetime.fromisoformat(expected[machine]['event_timestamp'])
    assert sha256(registry.read_bytes()).hexdigest()==before
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps({'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'project':online['project'],'machines':len(ids),'fields':fields,'registry_sha256_before':before,'registry_sha256_after':before,'online_dict':raw,'online_proto':native,'expected_latest_rows':[expected[m] for m in ids],'scope':'Read-only native20-machine monitoring lookup; exactFloat64 values/PRESENT statuses/event timestamps. Historical simulated event clock; not current physical sensors or model rolling inputs.'},indent=2,allow_nan=False)+'\n');print(json.dumps({'passed':True,'machines':len(ids),'field_values':len(ids)*len(fields),'all_native_statuses':'PRESENT','all_event_timestamps_match':True}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['state','offline-report','online-report','output']:parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();inspect(args.state,args.offline_report,args.online_report,args.output)
