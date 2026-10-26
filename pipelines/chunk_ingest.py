"""Ingest validated chunked JSONL into typed Snappy Parquet, verify exact readback and measure one query."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import platform
import re
import statistics
import time

from pyspark.sql import SparkSession, Window, functions as F

from generator.chunked import TABLES
from generator.config import iso_ts
from pipelines.chunk_source import ChunkSource, outside_source
from pipelines.transforms import TELEMETRY_FIELDS, FAILURE_FIELDS, MAINTENANCE_FIELDS, require, struct_schema


MACHINE_FIELDS=[(name,'string') for name in ('machine_id','factory_id','machine_type')]
MACHINE_FIELDS += [(name,'timestamp') for name in ('installed_at','valid_from_ts','valid_to_ts')]
MACHINE_FIELDS += [('rated_rpm','int'),('is_current','boolean')]
SCHEMAS={'machines':MACHINE_FIELDS,'maintenance':MAINTENANCE_FIELDS,'failures':FAILURE_FIELDS,
         'v1':TELEMETRY_FIELDS,'v2':TELEMETRY_FIELDS}
PAYLOAD=[name for name,_ in TELEMETRY_FIELDS]


def save_json(path: Path, value: dict):
    """Serialize actual UTC timestamps and allow no nonfinite numeric evidence."""
    def encode(item):
        """Represent Spark UTC timestamps, which are returned as naive Python datetimes."""
        if isinstance(item,datetime):
            return item.isoformat()+'Z' if item.tzinfo is None else item.isoformat()
        raise TypeError(type(item).__name__)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,default=encode,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def read_table(spark: SparkSession, source: ChunkSource, table: str):
    """Use a distributed directory scan after catalog verification, without collecting JSON rows or paths."""
    schema=struct_schema(SCHEMAS[table])
    if source.manifest['tables'][table]['parts']:
        frame=(spark.read.schema(schema).option('mode','FAILFAST').option('pathGlobFilter','part-*.jsonl')
               .json(str(source.directory(table))))
    else:
        frame=spark.createDataFrame([],schema)
    pattern='('+re.escape(TABLES[table])+r'/part-[0-9]+\.jsonl)$'
    return (frame.withColumn('source_path',F.regexp_extract(F.input_file_name(),pattern,1))
            .withColumn('source_manifest_sha256',F.lit(source.manifest_sha256)))


def latest_readings(frame):
    """Reject payload conflicts and keep the latest export, with file provenance as a deterministic tie-break."""
    immutable=[name for name in PAYLOAD if name!='created_ts']
    variants=(frame.withColumn('_hash',F.sha2(F.to_json(F.struct(*immutable)),256))
              .groupBy('event_id').agg(F.countDistinct('_hash').alias('variants')).filter('variants>1'))
    require(variants.limit(1).count()==0,'conflicting duplicate telemetry payload')
    order=Window.partitionBy('event_id').orderBy(F.col('created_ts').desc(),F.col('source_path'))
    return frame.withColumn('_rank',F.row_number().over(order)).filter('_rank=1').drop('_rank')


def exact_rows_equal(left, right, columns: list[str]):
    """Compare both directions as multisets, preserving duplicate multiplicity and null values."""
    require(left.select(*columns).exceptAll(right.select(*columns)).limit(1).count()==0,'Parquet lost/changed source rows')
    require(right.select(*columns).exceptAll(left.select(*columns)).limit(1).count()==0,'Parquet introduced source rows')


def write_parquet(frame, path: Path, partitions: int):
    """Write a new bounded-partition Snappy dataset, never overwrite a previous run."""
    (frame.repartition(partitions).write.mode('errorifexists').option('compression','snappy')
     .option('maxRecordsPerFile',500_000).parquet(str(path)))


def known_answer_checks(spark: SparkSession, output: Path) -> list[str]:
    """Exercise latest-created, replay, conflict, firmware nulls and exact Parquet roundtrip on known rows."""
    schema=struct_schema(TELEMETRY_FIELDS)
    first={'event_id':'A','machine_id':'P-001','operating_status':'operating','event_ts':'2026-10-01T00:00:00Z',
           'received_ts':'2026-10-01T00:00:01Z','created_ts':'2026-10-01T00:00:01Z',
           'temperature_c':10.0,'vibration_rms_mm_s':1.0,'current_a':2.0,'rpm':3000.0,'schema_version':1}
    extra={**first,'created_ts':'2026-10-01T01:00:01Z'}
    second={**first,'event_id':'B','schema_version':2,'pressure_bar':3.0,'temperature_c':20.0}
    raw=spark.createDataFrame([(json.dumps(row),path) for row,path in [(first,'part-a'),(extra,'part-b'),(second,'part-c')]],['payload','source_path'])
    frame=raw.select(F.from_json('payload',schema,{'mode':'FAILFAST'}).alias('r'),'source_path').select('r.*','source_path')
    unique=latest_readings(frame)
    require(unique.count()==2 and unique.filter("event_id='A'").first().source_path=='part-b','latest-created known answer failed')
    require(latest_readings(frame.unionByName(frame)).count()==2,'replay inflated unique rows')
    require(unique.filter('schema_version=1 AND pressure_bar IS NULL').count()==1 and unique.filter('pressure_bar=3').count()==1,'firmware null boundary failed')
    conflict=frame.filter("event_id='A'").limit(1).withColumn('temperature_c',F.lit(999.0))
    try:
        latest_readings(frame.unionByName(conflict))
    except ValueError as error:
        require('conflicting' in str(error),'unexpected conflict failure')
    else:
        raise ValueError('conflicting payload was accepted')
    target=output/'_checks'/'roundtrip'
    write_parquet(frame,target,1)
    exact_rows_equal(frame,spark.read.parquet(str(target)),PAYLOAD+['source_path'])
    return ['latest_export_across_parts','replay_preserves_unique_keys','schema_v1_null_v2_pressure',
            'conflicting_payload_rejected','exact_multiset_parquet_roundtrip']


def codec_inventory(spark: SparkSession, directory: Path) -> dict:
    """Inspect actual Parquet footer codecs through Spark's bundled Java reader; no host Arrow required."""
    files=list(directory.rglob('*.parquet'))
    codecs=set()
    footer_rows=0
    row_groups=0
    for path in files:
        metadata=spark._jvm.org.apache.parquet.hadoop.ParquetFileReader.readFooter(
            spark._jsc.hadoopConfiguration(),spark._jvm.org.apache.hadoop.fs.Path(str(path)))
        blocks=metadata.getBlocks().iterator()
        while blocks.hasNext():
            block=blocks.next()
            footer_rows+=block.getRowCount()
            row_groups+=1
            columns=block.getColumns().iterator()
            while columns.hasNext():
                codecs.add(str(columns.next().getCodec()))
    return {'files':len(files),'bytes':sum(p.stat().st_size for p in files),'observed_footer_codecs':sorted(codecs),
            'footer_rows':footer_rows,'row_groups':row_groups}


def measure_query(json_frame, parquet_frame, config, repetitions: int) -> dict:
    """Compare the same filtered projection/aggregate on identical duplicate-preserving data, with no Spark cache."""
    start=config.start+(config.end-config.start)*0.4
    end=config.start+(config.end-config.start)*0.45
    timings={'jsonl':[],'parquet_snappy':[]}
    expected=None
    results={}
    plans={}
    for repeat in range(repetitions):
        order=[('jsonl',json_frame),('parquet_snappy',parquet_frame)]
        if repeat%2:
            order.reverse()
        for name,frame in order:
            query=(frame.filter((F.col('event_ts')>=F.lit(iso_ts(start)).cast('timestamp')) &
                                (F.col('event_ts')<F.lit(iso_ts(end)).cast('timestamp')))
                   .agg(F.count('*').alias('rows'),F.sum('temperature_c').alias('temperature_sum'),
                        F.max('vibration_rms_mm_s').alias('vibration_max')))
            started=time.perf_counter()
            result=query.first().asDict()
            timings[name].append(time.perf_counter()-started)
            plans[name]=query._jdf.queryExecution().executedPlan().toString()
            if expected is None:
                expected=result
            require(result['rows']==expected['rows'] and math.isclose(result['temperature_sum'],expected['temperature_sum'],rel_tol=1e-10,abs_tol=1e-6)
                    and result['vibration_max']==expected['vibration_max'],'storage comparison returned different answers')
            results[name]=result
    return {'repetitions':repetitions,'seconds':timings,'median_seconds':{k:statistics.median(v) for k,v in timings.items()},
            'results':results,'window_start':iso_ts(start),'window_end_exclusive':iso_ts(end),'physical_plans':plans,
            'scope':'Same query/data, no Spark cache; alternating execution order. OS/file cache and other apps uncontrolled; not a general performance guarantee.'}


def run_ingest(spark: SparkSession, source: ChunkSource, output: Path, partitions: int, repeats: int, ui_pause: int) -> dict:
    """Persist Bronze/Silver and verify the actual payloads, grain, firmware and latest-export semantics."""
    started=time.perf_counter()
    output.mkdir(parents=True,exist_ok=False)
    checks=known_answer_checks(spark,output)
    print(f'PASS: {len(checks)} Spark known-answer checks',flush=True)
    telemetry=read_table(spark,source,'v1').unionByName(read_table(spark,source,'v2'))
    telemetry_path=output/'bronze'/'raw_telemetry'
    write_parquet(telemetry.withColumn('ingested_at',F.current_timestamp()),telemetry_path,partitions)
    bronze=spark.read.parquet(str(telemetry_path))
    exact_rows_equal(telemetry,bronze,PAYLOAD+['source_path','source_manifest_sha256'])
    checks.append('all_telemetry_rows_exact_after_parquet')
    stored=source.manifest['tables']['v1']['rows']+source.manifest['tables']['v2']['rows']
    require(bronze.count()==stored,'Bronze telemetry count differs')
    require(bronze.filter("source_path='' OR source_manifest_sha256 IS NULL").limit(1).count()==0,'Bronze source provenance missing')
    schema_rows=[row.asDict() for row in bronze.groupBy('schema_version').agg(F.count('*').alias('rows'),F.count('pressure_bar').alias('pressure_nonnull')).orderBy('schema_version').collect()]
    for row in schema_rows:
        require(row['pressure_nonnull']==(0 if row['schema_version']==1 else row['rows']),'firmware pressure null semantics changed')
    checks.extend(['bronze_counts_match_catalog','v1_pressure_null_v2_pressure_present'])
    inventories={'bronze/raw_telemetry':codec_inventory(spark,telemetry_path)}
    metadata={}
    for name in ('machines','failures','maintenance'):
        original=read_table(spark,source,name)
        target=output/'bronze'/f'raw_{name}'
        write_parquet(original,target,1)
        loaded=spark.read.parquet(str(target))
        exact_rows_equal(original,loaded,[n for n,_ in SCHEMAS[name]]+['source_path','source_manifest_sha256'])
        metadata[name]=loaded.count()
        require(metadata[name]==source.manifest['tables'][name]['rows'],'metadata count differs')
        require(loaded.filter("source_path=''").limit(1).count()==0,'metadata provenance missing')
        inventories[f'bronze/raw_{name}']=codec_inventory(spark,target)
    checks.append('all_metadata_rows_exact_after_parquet')
    silver_path=output/'silver'/'stg_telemetry'
    write_parquet(latest_readings(bronze),silver_path,partitions)
    silver=spark.read.parquet(str(silver_path))
    require(silver.count()==source.config.unique_rows,'Silver unique count differs')
    require(silver.groupBy('event_id').count().filter('count!=1').limit(1).count()==0,'Silver event IDs are not unique')
    maxima=bronze.groupBy('event_id').agg(F.max('created_ts').alias('expected_created'))
    require(silver.join(maxima,'event_id').filter('created_ts!=expected_created').limit(1).count()==0,'Silver failed latest export rule')
    machines=spark.read.parquet(str(output/'bronze'/'raw_machines')).select('machine_id')
    require(silver.join(machines,'machine_id','left_anti').limit(1).count()==0,'Silver machine FK missing')
    checks.extend(['silver_unique_grain','silver_latest_export','silver_machine_foreign_key'])
    inventories['silver/stg_telemetry']=codec_inventory(spark,silver_path)
    require(all(v['observed_footer_codecs']==['SNAPPY'] or v['footer_rows']==0 for v in inventories.values()),'unexpected Parquet codec')
    checks.append('actual_parquet_footer_codecs_snappy')
    sample=silver.orderBy('event_ts','machine_id').first().asDict()
    copies=[row.asDict() for row in bronze.filter(F.col('event_id')==sample['event_id']).orderBy('created_ts').limit(3).collect()]
    if ui_pause:
        print(f'UI ready: http://127.0.0.1:14040; waiting {ui_pause}s',flush=True)
        time.sleep(ui_pause)
    spark.catalog.clearCache()
    query=measure_query(telemetry,bronze,source.config,repeats)
    source.unchanged()
    cgroup={}
    for name in ('memory.current','memory.peak'):
        path=Path('/sys/fs/cgroup')/name
        if path.exists():
            cgroup[name]=int(path.read_text().strip())
    return {'passed':True,'checks':checks,'source_manifest_sha256':source.manifest_sha256,
            'receipt_sha256':source.receipt_sha256,'config':source.config.to_dict(),
            'source_offline_bytes':source.offline_bytes,'source_total_bytes':source.total_bytes,
            'bronze_telemetry_rows':stored,'silver_unique_rows':source.config.unique_rows,
            'duplicates_removed':stored-source.config.unique_rows,'schema_rows':schema_rows,'metadata_rows':metadata,
            'storage':inventories,'query_comparison':query,'sample_trace':{'silver':sample,'bronze_copies':copies},
            'spark_version':spark.version,'python':platform.python_version(),'application_id':spark.sparkContext.applicationId,
            'job_wall_seconds':time.perf_counter()-started,'cgroup_memory_before_shutdown':cgroup,
            'scope':'Local[2] typed JSONL to plain Snappy Parquet folders; no PostgreSQL bulk staging, feature/model retraining, Airflow deployment or >=100GB claim.'}


def main():
    """Run one immutable source/output pair in the existing pinned Spark container."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--partitions',type=int,default=8)
    parser.add_argument('--query-repetitions',type=int,default=3)
    parser.add_argument('--ui-pause-seconds',type=int,default=0)
    args=parser.parse_args()
    require(1<=args.partitions<=64 and 1<=args.query_repetitions<=5 and 0<=args.ui_pause_seconds<=60,'invalid bounded job configuration')
    outside_source(args.source,args.output)
    outside_source(args.source,args.report)
    require(not args.output.exists() and not args.report.exists(),'output/report exists; choose a new run')
    source=ChunkSource(args.source,args.receipt)
    spark=(SparkSession.builder.appName('iot-chunk-parquet-ingest').config('spark.sql.session.timeZone','UTC')
           .config('spark.sql.shuffle.partitions',str(args.partitions)).getOrCreate())
    spark.sparkContext.setLogLevel('WARN')
    try:
        report=run_ingest(spark,source,args.output,args.partitions,args.query_repetitions,args.ui_pause_seconds)
        save_json(args.report,report)
        save_json(args.output/'_RUN_SUCCESS.json',{'passed':True,'source_manifest_sha256':source.manifest_sha256,'report':str(args.report)})
        print(f"PASS: chunked ingest; Bronze={report['bronze_telemetry_rows']}, Silver={report['silver_unique_rows']}, checks={len(report['checks'])}",flush=True)
    finally:
        spark.stop()


if __name__=='__main__':
    main()
