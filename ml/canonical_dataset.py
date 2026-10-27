"""Canonicalize verified native Feast snapshots by stable id before incremental content-addressed versioning."""
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from ml.protocol import load_protocol,PROTOCOL_SHA256
from ml.snapshot import snapshot_id
from ml.training_contract import digest,save,verify_descriptor


def canonical_table(frame,columns,schema):
    """Make row/column order and physical types explicit, without changing any model input values."""
    return pa.Table.from_pandas(frame.sort_values('id')[columns],schema=schema,preserve_index=False,safe=True).replace_schema_metadata(None)


def run(args):
    """Validate every feature/label/key row, then write deterministic daily Parquet outside the source tree."""
    cohort=verify_descriptor(args.cohort,'feast-cohort');protocol=load_protocol(args.protocol)
    report_path=Path(cohort['report_path'])
    if digest(report_path)!=cohort['report_sha256']:raise ValueError('Native source report changed')
    report=json.loads(report_path.read_text())
    if not report['passed'] or report['native_rows_this_invocation']!=3_132_184:raise ValueError('Require a complete fresh native Feast load')
    source=Path(cohort['path']).parent;args.destination.mkdir(parents=True,exist_ok=False)
    fields=protocol['feature_allowlist'];keys=['id','machine_id','event_timestamp','label_available_ts','failure_episode_id','split']
    columns=[*keys,'failure_in_next_1h',*fields]
    key_schema=pa.schema([(n,pa.timestamp('us',tz='UTC') if n in ['event_timestamp','label_available_ts'] else pa.string()) for n in keys])
    schema=pa.schema([*key_schema,pa.field('failure_in_next_1h',pa.uint8()),*[pa.field(n,pa.float64()) for n in fields]])
    label_schema=pa.schema([('id',pa.string()),('label',pa.uint8())]);parts=[];total=0
    for path in sorted((source/'feast-cohort').glob('day=*/data.parquet')):
        frame=pq.ParquetFile(path).read().to_pandas();day=path.parent.name
        if set(frame.columns)!=set(columns) or frame.id.duplicated().any():raise ValueError('Unexpected cohort schema or duplicate ids')
        if any(str(frame[n].dtype)!='datetime64[us, UTC]' for n in ['event_timestamp','label_available_ts']):raise ValueError('Require exact UTC microsecond input timestamps')
        if not (frame.event_timestamp.dt.strftime('day=%Y-%m-%d')==day).all():raise ValueError('Cross-day or misplaced snapshot')
        ids=[snapshot_id(m,t.to_pydatetime()) for m,t in zip(frame.machine_id,frame.event_timestamp)]
        if ids!=frame.id.tolist() or not frame.failure_in_next_1h.isin([0,1]).all():raise ValueError('Snapshot identity or label changed')
        if not np.isfinite(frame[fields].to_numpy(dtype='float64')).all():raise ValueError('Nonfinite feature')
        labels=pq.ParquetFile(source/'labels'/day/'labels.parquet').read().to_pandas()
        if list(labels.columns)!=['id','label']:raise ValueError('Require separate2column label table')
        joined=frame[['id','failure_in_next_1h']].merge(labels,on='id',validate='one_to_one',how='outer')
        if len(joined)!=len(frame) or not (joined.failure_in_next_1h==joined.label).all():raise ValueError('Canonicalization changed label grain/truth')
        tables={'cohort':canonical_table(frame,columns,schema),
            'labels':canonical_table(labels,['id','label'],label_schema),
            'snapshot-keys':canonical_table(frame,keys,key_schema)}
        for kind,table in tables.items():
            target=args.destination/kind/day/'data.parquet';target.parent.mkdir(parents=True,exist_ok=False)
            pq.write_table(table,target,compression='snappy',version='2.6',row_group_size=65536,
                use_dictionary=[n for n in ['machine_id','failure_episode_id','split'] if n in table.column_names],write_statistics=True)
            restored=pq.ParquetFile(target).read()
            if not restored.equals(table,check_metadata=True):raise ValueError('Canonical Parquet readback changed values')
            parts.append({'path':target.relative_to(args.destination).as_posix(),'sha256':digest(target),'bytes':target.stat().st_size,'rows':len(frame)})
        total+=len(frame)
    if total!=3_132_184 or len(parts)!=180:raise ValueError('Incomplete canonical dataset')
    metadata={'canonical_format':'iot-feast-canonical-daily-v1','protocol_sha256':PROTOCOL_SHA256,
        'source_manifest_sha256':cohort['source_manifest_sha256'],'feature_allowlist':fields,
        'pyarrow_version':pa.__version__,'row_order':'id ascending per event UTC day','label_columns':['id','label']}
    save(args.output,{'passed':True,'rows':total,'files':parts,'metadata':metadata,
        'native_descriptor_sha256':digest(args.cohort),'native_report_sha256':cohort['report_sha256'],
        'code_sha256':digest(Path(__file__)),'scope':'Actual native Feast data canonicalized and every Parquet value read back exactly. No model tuning; content-store publication is a separate step.'})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['cohort','protocol','destination','output']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
