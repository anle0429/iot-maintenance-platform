"""Register evaluated weights with an immutable incremental MLflow dataset reference instead of another full data upload."""
import argparse,json,tempfile
from pathlib import Path
import mlflow
from mlflow.models import infer_signature
from mlflow.tracking import MlflowClient
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from ml.portable_model import PortablePumpModel
from ml.protocol import load_protocol,PROTOCOL_SHA256
from ml.training_contract import verify_descriptor,digest,save
from ml.model_data_link import verify_model_data_link,verify_source_snapshot


def run(args):
    """Verify source/data/model identity, upload model-only artifacts and check registry restoration before aliasing."""
    if args.output.exists():raise ValueError('Preserve the completed registration receipt')
    verify_source_snapshot(json.loads(args.source_binding.read_text()),Path(__file__).resolve().parents[1],args.source_commit)
    evaluation=verify_descriptor(args.evaluation,'holdout-evaluation');selection_path=Path(evaluation['selection_descriptor'])
    if digest(selection_path)!=evaluation['selection_sha256']:raise ValueError('Selection changed after evaluation')
    selected=verify_descriptor(selection_path,'validation-selected-model');partitions_path=Path(selected['split_descriptor'])
    if digest(partitions_path)!=selected['split_descriptor_sha256']:raise ValueError('Model data split changed')
    partitions=verify_descriptor(partitions_path,'temporal-split');canonical=json.loads(args.canonical_report.read_text())
    local=json.loads(args.local_version.read_text());remote=json.loads(args.remote_version.read_text())
    if local['canonical_report_sha256']!=digest(args.canonical_report):raise ValueError('Local version belongs to a different canonical report')
    link=verify_model_data_link(partitions,canonical,local,remote);protocol=load_protocol(args.protocol)
    if canonical['rows']!=3_132_184 or len(canonical['files'])!=180:raise ValueError('Require the complete declared native cohort')
    mlflow.set_tracking_uri(args.tracking_uri);client=MlflowClient();data_run=client.get_run(link['data_run_id'])
    if data_run.info.status!='FINISHED' or data_run.data.tags.get('data_version')!=link['data_version'] or data_run.data.tags.get('locations_sha256')!=link['locations_sha256']:
        raise ValueError('The completed remote dataset reference changed')
    with tempfile.TemporaryDirectory() as directory:
        actual=Path(client.download_artifacts(link['data_run_id'],'dataset-version/manifest.json',directory))
        if digest(actual)!=link['data_version']:raise ValueError('Remote dataset manifest bytes differ')
    first=next((Path(evaluation['artifacts'][0]['path'])/'predictions').glob('*.parquet'))
    sample=pq.ParquetFile(first).read_row_group(0).to_pandas().iloc[:200];fields=protocol['feature_allowlist'];features=sample[fields].astype('float64')
    portable=PortablePumpModel();portable.load_context(type('Context',(),{'artifacts':{'portable':selected['portable_path']}})())
    predicted=portable.predict(None,features)
    if len(sample)!=200 or not np.allclose(predicted.risk_score.to_numpy(),sample.risk_score.to_numpy(),rtol=0,atol=1e-12):raise ValueError('Portable model differs from sealed native predictions')
    mlflow.set_experiment('iot-medium-60d-v1')
    context={'protocol_sha256':PROTOCOL_SHA256,'evaluation_sha256':digest(args.evaluation),'selection_sha256':evaluation['selection_sha256'],
        'training_data_version':link['data_version'],'dataset_run_id':link['data_run_id'],'source_snapshot_commit':args.source_commit,
        'native_report_sha256':link['native_report_sha256'],'deployment_status':'evaluation_passed_not_production','synthetic_data':'true'}
    if args.existing_version:
        version=client.get_model_version('iot-pump-failure-v1',args.existing_version);run_id=version.run_id;prior=client.get_run(run_id)
        if prior.info.status!='FINISHED' or any(prior.data.tags.get(k)!=v for k,v in context.items()):raise ValueError('Existing version context differs')
    else:
        with mlflow.start_run(run_name=args.run) as active:
            run_id=active.info.run_id;mlflow.set_tags(context)
            mlflow.log_params({'algorithm':protocol['baseline']['algorithm'],'max_iter':protocol['baseline']['max_iter'],
                'reg_param':protocol['baseline']['reg_param'],'fixed_threshold':evaluation['fixed_threshold'],'features':','.join(fields),
                'scaler_fit_partition':'train','threshold_selection_partition':'validation','data_storage':'incremental-content-addressed',
                'class_weights':json.dumps(selected['train_class_weights'],sort_keys=True),
                'scaler_with_mean':protocol['baseline']['standard_scaler_with_mean'],'scaler_with_std':protocol['baseline']['standard_scaler_with_std']})
            for name in ['validation','test','constant_training_prevalence_comparator']:
                mlflow.log_metrics({name+'.'+k:float(v) for k,v in evaluation[name].items() if type(v) in [int,float]})
            for path in [args.protocol,args.evaluation,selection_path,partitions_path,args.canonical_report,args.local_version,args.source_binding]:mlflow.log_artifact(str(path),'lineage')
            mlflow.log_dict(link,'lineage/incremental-data-reference.json')
            mlflow.log_artifacts(selected['model_path'],'native-spark-model');mlflow.log_artifact(selected['portable_path'],'portable')
            info=mlflow.pyfunc.log_model(name='pump-failure',python_model=PortablePumpModel(),artifacts={'portable':selected['portable_path']},
                code_paths=[str(Path(__file__).parent)],signature=infer_signature(features,predicted),input_example=features.iloc[:2],
                pip_requirements=['mlflow=='+mlflow.__version__,'numpy=='+np.__version__,'pandas=='+pd.__version__])
            version=mlflow.register_model(info.model_uri,'iot-pump-failure-v1')
            client.set_model_version_tag('iot-pump-failure-v1',version.version,'production','false')
            client.set_model_version_tag('iot-pump-failure-v1',version.version,'data_version',link['data_version'])
    restored=mlflow.pyfunc.load_model('models:/iot-pump-failure-v1/'+str(version.version)).predict(features)
    delta=float(np.max(np.abs(restored.risk_score.to_numpy()-sample.risk_score.to_numpy())))
    if delta>1e-12:raise ValueError('Restored registry weights differ from actual Spark scores')
    if any(x.path=='data' for x in client.list_artifacts(run_id)):raise ValueError('Model run unexpectedly contains another full dataset copy')
    client.set_registered_model_alias('iot-pump-failure-v1','candidate',version.version)
    client.set_model_version_tag('iot-pump-failure-v1',version.version,'latest','true')
    for prior in client.search_model_versions("name='iot-pump-failure-v1'"):
        if prior.version!=version.version and prior.tags.get('latest')=='true':client.set_model_version_tag('iot-pump-failure-v1',prior.version,'latest','false')
    save(args.output,{'passed':True,'model_name':'iot-pump-failure-v1','version':version.version,'model_run_id':run_id,
        'source_snapshot_commit':args.source_commit,'incremental_data_reference':link,'model_data_artifact_payload_bytes':0,
        'registry_model_native_spark_probe_rows':len(sample),'max_abs_probability_difference':delta,'production':False,'alias':'candidate',
        'code_sha256':digest(Path(__file__)),'portable_sha256':digest(Path(selected['portable_path'])),
        'scope':'Actual model weights/params/metrics registered; full dataset is referenced through verified incremental MLflow data run, not uploaded again. Source commit names matching execution bytes, not inferred runtime git HEAD. Serving remains version1.'})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['evaluation','canonical-report','local-version','remote-version','protocol','output','source-binding']:parser.add_argument('--'+name,type=Path,required=True)
    for name in ['tracking-uri','source-commit','run']:parser.add_argument('--'+name,required=True)
    parser.add_argument('--existing-version');run(parser.parse_args())
