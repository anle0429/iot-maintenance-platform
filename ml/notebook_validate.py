"""Reload the saved Spark/portable models and verify validation metrics without reopening test rows."""
import argparse,json
from pathlib import Path
from pyspark.ml import PipelineModel
from pyspark.sql import SparkSession
from ml.protocol import load_protocol,PROTOCOL_SHA256
from ml.training_contract import digest,score_portable,save,verify_descriptor
from ml.training_job import confusion,with_probability


def run(args):
    """Check serialized model behavior against the sealed validation selection on the same fixed partition."""
    selected=verify_descriptor(args.selection,'validation-selected-model')
    partition_path=Path(selected['split_descriptor'])
    if digest(partition_path)!=selected['split_descriptor_sha256'] or selected['holdout_rows_opened']!=0:
        raise ValueError('Selection was changed or holdout used during selection')
    partitions=verify_descriptor(partition_path,'temporal-split');protocol=load_protocol(args.protocol)
    portable=json.loads(Path(selected['portable_path']).read_text());threshold=selected['validation']['threshold']
    spark=SparkSession.builder.appName('iot-notebook-validation-reload').config('spark.sql.session.timeZone','UTC').getOrCreate()
    spark.sparkContext.setLogLevel('WARN')
    try:
        model=PipelineModel.load(selected['model_path'])
        validation=with_probability(model.transform(spark.read.parquet(partitions['split_paths']['validation']))).cache()
        actual=confusion(validation,threshold)
        if any(actual[k]!=selected['validation'][k] for k in actual):raise ValueError('Restored model changed validation metrics')
        sample=validation.select(*protocol['feature_allowlist'],'risk_score').limit(200).collect()
        delta=max(abs(score_portable([r[n] for n in protocol['feature_allowlist']],portable)-r.risk_score) for r in sample)
        if len(sample)!=200 or delta>1e-12:raise ValueError('Saved portable model differs from restored native Spark model')
        save(args.output,{'passed':True,'protocol_sha256':PROTOCOL_SHA256,'validation':selected['validation'],
            'restored_validation_confusion':actual,'portable_native_probe_rows':len(sample),'max_abs_probability_difference':delta,
            'selection_sha256':digest(args.selection),'portable_sha256':digest(Path(selected['portable_path'])),
            'holdout_rows_opened':0,'code_sha256':digest(Path(__file__)),
            'scope':'Validation only, threshold fixed before reload; no new holdout evaluation or model tuning.'})
        validation.unpersist()
    finally:spark.stop()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for n in ['selection','protocol','output']:parser.add_argument('--'+n,type=Path,required=True)
    run(parser.parse_args())
