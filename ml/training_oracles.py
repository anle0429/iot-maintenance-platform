"""Run known-answer Spark time-cut/model oracles independently of the real Feast cohort or test metrics."""

import argparse,sys,json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pyspark.sql import SparkSession
from ml.protocol import PROTOCOL_SHA256,load_protocol
from ml.training_contract import digest,inventory,save
from ml.training_job import split,train,evaluate


def run(args):
    """Create an explicitly synthetic linear fixture with valid and deliberately purged cutoff cases."""
    args.workspace.mkdir(parents=True,exist_ok=False);protocol=load_protocol(args.protocol)
    spark=SparkSession.builder.appName('iot-training-known-answers').config('spark.sql.session.timeZone','UTC').config('spark.sql.shuffle.partitions','4').getOrCreate()
    spark.sparkContext.setLogLevel('WARN')
    try:
        rows=[];fields=protocol['feature_allowlist']
        dates={'train':datetime(2026,10,2,tzinfo=timezone.utc),'validation':datetime(2026,11,13,tzinfo=timezone.utc),'test':datetime(2026,11,22,tzinfo=timezone.utc)}
        for name,start in dates.items():
            for i in range(120):
                label=i%2;t=start+timedelta(minutes=5*i)
                row={'machine_id':'fixture-A','event_timestamp':t,'label_available_ts':t+timedelta(hours=1,seconds=30),
                    'failure_episode_id':'fixture-'+name+'-'+str(i//4) if label else '',
                    'split':name,'failure_in_next_1h':label}
                row.update({n:float((2*label-1)*(j+1)) if j<5 else 1.0 for j,n in enumerate(fields)})
                rows.append(row)
        source=args.workspace/'fixture';spark.createDataFrame(rows).write.parquet(str(source/'day=fixture/data.parquet'))
        save(args.workspace/'source.json',{'passed':True,'scope':'Known-answer synthetic fixture only; not a native Feast retrieval'})
        cohort=args.workspace/'cohort.json'
        save(cohort,{'passed':True,'kind':'feast-cohort','protocol_sha256':PROTOCOL_SHA256,'path':str(source),
            'report_path':str(args.workspace/'source.json'),'report_sha256':digest(args.workspace/'source.json'),
            'source_manifest_sha256':'fixture-not-real-source','split_counts':[{'split':n,'rows':120,'positive_rows':60} for n in dates],
            'artifacts':[{'path':str(source),'files':inventory(source)}]})
        parts=args.workspace/'parts.json';selection=args.workspace/'selection.json';evaluation=args.workspace/'evaluation.json'
        split(spark,SimpleNamespace(input=cohort,workspace=args.workspace,output=parts),protocol)
        # A mislabeled cut must fail before creating any split output.
        broken=[{**rows[0],'event_timestamp':datetime(2026,11,11,23,tzinfo=timezone.utc),'label_available_ts':datetime(2026,11,12,tzinfo=timezone.utc)}]
        bad=args.workspace/'bad-fixture';spark.createDataFrame(broken).write.parquet(str(bad/'day=fixture/data.parquet'))
        bad_descriptor=args.workspace/'bad-cohort.json';data=json.loads(cohort.read_text());data['path']=str(bad);data['artifacts']=[{'path':str(bad),'files':inventory(bad)}];save(bad_descriptor,data)
        rejected=False
        try:split(spark,SimpleNamespace(input=bad_descriptor,workspace=args.workspace/'rejected',output=args.workspace/'must-not-exist.json'),protocol)
        except ValueError as error:
            if 'purge violation' not in str(error):raise
            rejected=True
        if not rejected or (args.workspace/'must-not-exist.json').exists():raise ValueError('Known label-cut violation was not rejected')
        train(spark,SimpleNamespace(input=parts,workspace=args.workspace,output=selection),protocol)
        evaluate(spark,SimpleNamespace(input=selection,workspace=args.workspace,output=evaluation),protocol)
        result=json.loads(evaluation.read_text());fitted=json.loads(selection.read_text())
        if result['test']['tp']!=60 or result['test']['tn']!=60 or result['test']['fp'] or result['test']['fn']:
            raise ValueError('The known separable fixture did not produce its expected confusion counts')
        if fitted['holdout_rows_opened']!=0 or result['portable_spark_max_abs_probability_difference']>1e-12:
            raise ValueError('Model isolation or portable prediction oracle failed')
        save(args.output,{'passed':True,'checks':{'time_cut_purge_rejected':rejected,'known_linear_confusion_60_60':True,
            'portable_model_matches_spark':True,'threshold_sealed_before_holdout':True},'oracle_code_sha256':digest(Path(__file__)),
            'job_code_sha256':digest(Path(__file__).with_name('training_job.py')),'selection_sha256':digest(selection),
            'evaluation_sha256':digest(evaluation),'scope':'Explicit known-answer synthetic360-row Spark oracle. No native Feast/full cohort or real model performance claim.'})
    finally:spark.stop()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['workspace','output','protocol']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
