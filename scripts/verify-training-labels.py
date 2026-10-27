"""Read every exported label Parquet file and independently verify the complete id/label merge with DuckDB."""
import argparse,json
from pathlib import Path
from hashlib import sha256
import duckdb
import pyarrow.parquet as pq


def run(args):
    """Check physical two-column schema, global uniqueness, truth preservation and both join directions."""
    source=args.workspace/'input';labels=source/'labels';cohort=source/'feast-cohort';files=sorted(labels.glob('day=*/labels.parquet'))
    if len(files)!=60:raise ValueError('Expected60native daily label partitions')
    physical_rows=0;schemas=set()
    for p in files:
        meta=pq.ParquetFile(p);schema=tuple(meta.schema_arrow.names);schemas.add(schema)
        if schema!=('id','label'):raise ValueError('Label table must contain exactly id and label')
        physical_rows+=meta.metadata.num_rows
    database=duckdb.connect();database.execute("SET threads=1; SET memory_limit='2GB'")
    database.execute("SET temp_directory='"+str(args.scratch).replace("'","''")+"'")
    database.read_parquet(str(labels/'day=*/labels.parquet')).project('id,label').create_view('labels')
    database.read_parquet(str(cohort/'day=*/data.parquet')).project('id,machine_id,event_timestamp,failure_in_next_1h').create_view('cohort')
    total,unique,bad=database.execute('SELECT count(*),count(distinct id),count(*) FILTER(WHERE id IS NULL OR label IS NULL OR label NOT IN (0,1)) FROM labels').fetchone()
    missing,mismatch,joined=database.execute('SELECT count(*) FILTER(WHERE l.id IS NULL),count(*) FILTER(WHERE c.failure_in_next_1h<>l.label),count(*) FROM cohort c LEFT JOIN labels l USING(id)').fetchone()
    orphan=database.execute('SELECT count(*) FROM labels l ANTI JOIN cohort c USING(id)').fetchone()[0]
    if not(total==unique==physical_rows==joined==3_132_184 and bad==missing==mismatch==orphan==0):raise ValueError('Global label-grain/merge readback failed')
    sample=database.execute('SELECT c.id,c.machine_id,cast(c.event_timestamp AS VARCHAR) event_timestamp,l.label FROM cohort c JOIN labels l USING(id) ORDER BY c.machine_id,c.event_timestamp LIMIT 3').fetchall()
    report={'passed':True,'physical_label_columns':[list(s) for s in sorted(schemas)],'native_daily_label_files':len(files),
        'physical_rows':physical_rows,'global_unique_ids':unique,'joined_rows':joined,'invalid_labels':bad,'missing_labels':missing,
        'orphan_labels':orphan,'mismatched_labels':mismatch,'sample':[dict(zip(['id','machine_id','event_timestamp','label'],r)) for r in sample],
        'source_load_report_sha256':sha256((source/'load-report.json').read_bytes()).hexdigest(),
        'code_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),'scope':'Independent readback of all actual2-column label Parquet partitions and complete3.132Mcohort join. No new model training or changed labels.'}
    with args.output.open('x') as stream:stream.write(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2));database.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['workspace','scratch','output']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
