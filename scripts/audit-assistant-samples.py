"""Generate paired synthetic pump sources and export source-bound Spark features plus separate ID/label tables for assistant validation."""
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import json
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from generator.config import Config,iso_ts
from generator.simulation import generate
from generator.drift import SensorDrift,generate_drift
from generator.storage import write_dataset,write_json
from feature_store.training_samples import FEATURES,separate_tables,join_tables


def json_value(value):
    """Serialize Spark's UTC session timestamps explicitly rather than relying on the host timezone."""
    if isinstance(value,datetime):
        return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.astimezone(timezone.utc).isoformat()
    raise TypeError(type(value).__name__)


def rows(frame):
    """Collect only this explicitly small study's bounded source records with aware UTC timestamps."""
    return json.loads(json.dumps([r.asDict(recursive=True) for r in frame.collect()],default=json_value,allow_nan=False))


def write_jsonl(path,values):
    """Write a new reviewable table once without replacing a previous run."""
    with path.open('x',encoding='utf-8') as stream:
        for value in values:stream.write(json.dumps(value,sort_keys=True,allow_nan=False)+'\n')


def run(output,settings):
    """Compare genuine baseline/drift physical truth and compute availability-aware sample exports with the production Spark transforms."""
    from pyspark.sql import SparkSession,functions as F
    from pipelines.features import FEATURE_COLUMNS,compute_features,label_snapshots
    from pipelines.transforms import TELEMETRY_FIELDS,MACHINE_FIELDS,FAILURE_FIELDS,MAINTENANCE_FIELDS,struct_schema,deduplicate_telemetry
    if tuple(FEATURE_COLUMNS)!=FEATURES:raise ValueError('The export feature contract differs from the actual Spark pipeline')
    config=Config(**settings['simulation']);profile=SensorDrift(**settings['sensor_drift'])
    if config.unique_rows>20000:raise ValueError('This paired study is capped at20,000 unique events per version')
    output.mkdir(parents=True,exist_ok=False);write_json(output/'settings.json',settings)
    baseline=generate(config);shifted=generate_drift(config,profile)
    if baseline.failures!=shifted.failures or baseline.maintenance!=shifted.maintenance or baseline.machines!=shifted.machines:
        raise ValueError('Calibration drift changed latent physical truth')
    raw_deltas=[]
    for left,right in zip(baseline.telemetry,shifted.telemetry,strict=True):
        expected=profile.transform(left);expected['event_id']=right['event_id']
        if expected!=right:raise ValueError('The calibration transformation changed an undeclared field')
        raw_deltas.append({'event_ts':left['event_ts'],'temperature_delta':right['temperature_c']-left['temperature_c']})
    spark=(SparkSession.builder.master('local[2]').appName('iot-assistant-sample-validation')
           .config('spark.sql.session.timeZone','UTC').config('spark.sql.shuffle.partitions','4')
           .config('spark.ui.enabled','false').config('spark.sql.adaptive.enabled','false').getOrCreate())
    spark.sparkContext.setLogLevel('WARN');results={};started=time.monotonic()
    try:
        for name,dataset in [('baseline',baseline),('drift',shifted)]:
            folder=output/name;folder.mkdir();source=folder/'source';manifest=write_dataset(dataset,config,source)
            manifest['sensor_drift']=asdict(profile) if name=='drift' else None
            manifest['scope']='Small paired calibration study for assistant data validation; not the historical100GB run.'
            write_json(source/'manifest.json',manifest);dataset_id=sha256((source/'manifest.json').read_bytes()).hexdigest()
            telemetry=spark.read.schema(struct_schema(TELEMETRY_FIELDS)).json(str(source/'telemetry/*/*.jsonl'))
            telemetry=telemetry.withColumn('source_path',F.input_file_name()).withColumn('source_line',F.lit(1))
            silver=deduplicate_telemetry(telemetry).cache()
            if silver.count()!=config.unique_rows:raise ValueError('The native deduplicated source count differs')
            machines=spark.read.schema(struct_schema(MACHINE_FIELDS)).json(str(source/'machines.jsonl'))
            failures=spark.read.schema(struct_schema(FAILURE_FIELDS)).json(str(source/'failure_events.jsonl'))
            maintenance=spark.read.schema(struct_schema(MAINTENANCE_FIELDS)).json(str(source/'maintenance_events.jsonl'))
            snapshots=(silver.filter(F.pmod(F.unix_timestamp('event_ts')-int(config.start.timestamp()),F.lit(300))==0)
                       .select('machine_id',F.col('event_ts').alias('event_timestamp'),'operating_status').cache())
            labels=label_snapshots(snapshots,failures,maintenance,iso_ts(config.end)).cache()
            features=compute_features(snapshots.select('machine_id','event_timestamp'),silver,machines,failures).cache()
            if features.filter('max_received_ts>event_timestamp OR max_feature_event_ts>event_timestamp').count():
                raise ValueError('A feature includes an event or arrival unavailable at prediction time')
            feature_rows,label_rows=rows(features),rows(labels)
            tables=separate_tables(feature_rows,label_rows,dataset_id);joined=join_tables(tables['features'],tables['labels'])
            tables['features']=sorted(tables['features'],key=lambda r:(r['machine_id'],r['event_timestamp']))
            write_jsonl(folder/'feature-inputs.jsonl',feature_rows);write_jsonl(folder/'label-inputs.jsonl',label_rows)
            write_jsonl(folder/'features.jsonl',tables['features']);write_jsonl(folder/'labels.jsonl',tables['labels'])
            write_jsonl(folder/'joined-training.jsonl',joined)
            spark.createDataFrame([(r['sample_id'],r['failure_in_next_1h']) for r in tables['labels']],
                                  'sample_id string, failure_in_next_1h int').write.mode('errorifexists').parquet(str(folder/'labels.parquet'))
            readback=rows(spark.read.parquet(str(folder/'labels.parquet')))
            if sorted(readback,key=lambda r:r['sample_id'])!=sorted(tables['labels'],key=lambda r:r['sample_id']):
                raise ValueError('The native two-column target readback differs')
            proof={k:v for k,v in tables.items() if k not in {'features','labels'}}
            proof.update({'passed':True,'feature_rows':len(tables['features']),'target_columns':['sample_id','failure_in_next_1h'],
                          'joined_rows':len(joined),'source_unique_events':config.unique_rows,'failure_events':len(dataset.failures),
                          'feature_column_names':list(FEATURES),'parquet_readback_rows':len(readback)})
            write_json(folder/'verification.json',proof);results[name]=proof
            features.unpersist();labels.unpersist();snapshots.unpersist();silver.unpersist()
    finally:spark.stop()
    before={ (r['machine_id'],r['event_timestamp']):r['failure_in_next_1h'] for r in
            [json.loads(line) for line in (output/'baseline/label-inputs.jsonl').read_text().splitlines()]}
    after={ (r['machine_id'],r['event_timestamp']):r['failure_in_next_1h'] for r in
           [json.loads(line) for line in (output/'drift/label-inputs.jsonl').read_text().splitlines()]}
    if before!=after:raise ValueError('Calibration drift changed target truth')
    summary={'passed':True,'versions':results,'same_physical_truth_and_labels':True,
             'raw_temperature_delta_before_drift':max(abs(r['temperature_delta']) for r in raw_deltas if r['event_ts']<profile.start_ts),
             'maximum_raw_temperature_delta':max(r['temperature_delta'] for r in raw_deltas),
             'spark_seconds':time.monotonic()-started,'source_commit':settings['source_commit'],
             'scope':'Real small paired generator/Spark exports and native two-column Parquet readback. Not a new trained model, live stream,100GB result or deployed assistant tool.'}
    write_json(output/'verification.json',summary);print(json.dumps(summary))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--settings',type=Path,required=True)
    args=parser.parse_args();run(args.output,json.loads(args.settings.read_bytes()))
