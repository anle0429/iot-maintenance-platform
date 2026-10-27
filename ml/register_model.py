"""Version the sealed model, complete split data and lineage in a real database-backed MLflow registry."""

import argparse,json
from pathlib import Path
import tempfile

import mlflow
from mlflow.models import infer_signature
from mlflow.tracking import MlflowClient
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from ml.portable_model import PortablePumpModel
from ml.protocol import PROTOCOL_SHA256,load_protocol
from ml.training_contract import digest,inventory,save,verify_descriptor


def run(args):
    """Register only after fixed holdout evaluation; tag latest without claiming production approval."""
    if args.output.exists():raise ValueError('Preserve the registration receipt; inspect previous run before retrying')
    evaluation=verify_descriptor(args.evaluation,'holdout-evaluation');selection_path=Path(evaluation['selection_descriptor'])
    if digest(selection_path)!=evaluation['selection_sha256']:raise ValueError('Selection changed after evaluation')
    selected=verify_descriptor(selection_path,'validation-selected-model');split_path=Path(selected['split_descriptor'])
    if digest(split_path)!=selected['split_descriptor_sha256']:raise ValueError('Split changed after selection')
    partitions=verify_descriptor(split_path,'temporal-split');protocol=load_protocol(args.protocol)
    first=next((Path(evaluation['artifacts'][0]['path'])/'predictions').glob('*.parquet'))
    sample=pq.ParquetFile(first).read_row_group(0).to_pandas().iloc[:200]
    fields=protocol['feature_allowlist'];features=sample[fields].astype('float64')
    portable=PortablePumpModel();portable.load_context(type('Context',(),{'artifacts':{'portable':selected['portable_path']}})())
    predicted=portable.predict(None,features)
    if len(features)!=200 or not np.allclose(predicted.risk_score.to_numpy(),sample.risk_score.to_numpy(),rtol=0,atol=1e-12):
        raise ValueError('Portable Python model differs from sealed actual Spark holdout predictions')
    mlflow.set_tracking_uri(args.tracking_uri);mlflow.set_experiment('iot-medium-60d-v1');client=MlflowClient()
    with mlflow.start_run(run_name=args.run) as active:
        run_id=active.info.run_id
        mlflow.set_tags({'protocol_sha256':PROTOCOL_SHA256,'source_manifest_sha256':partitions['source_manifest_sha256'],
            'selection_sha256':evaluation['selection_sha256'],'evaluation_sha256':digest(args.evaluation),
            'training_data_version':partitions['native_report_sha256'],'code_git_commit':args.code_commit,
            'synthetic_data':'true','deployment_status':'evaluation_passed_not_production'})
        mlflow.log_params({'algorithm':protocol['baseline']['algorithm'],'max_iter':40,'reg_param':0.1,
            'scaler_fit_partition':'train','threshold_selection_partition':'validation','fixed_threshold':evaluation['fixed_threshold'],
            'features':','.join(fields),'class_weights':json.dumps(selected['train_class_weights'],sort_keys=True)})
        for name in ['validation','test','constant_training_prevalence_comparator']:
            mlflow.log_metrics({name+'.'+key:float(value) for key,value in evaluation[name].items() if type(value) in [int,float]})
        for name,item in partitions['counts'].items():
            mlflow.log_metrics({name+'.'+key:float(value) for key,value in item.items() if type(value) in [int,float]})
        for path in [args.protocol,args.evaluation,selection_path,split_path]:mlflow.log_artifact(str(path),'lineage')
        mlflow.log_dict(partitions,'lineage/data-inventory.json')
        mlflow.log_artifacts(selected['model_path'],'native-spark-model')
        # Upload actual split Parquet bytes, in addition to their versioned digest inventory.
        for name,path in partitions['split_paths'].items():mlflow.log_artifacts(path,'data/'+name)
        mlflow.log_artifact(selected['portable_path'],'portable')
        model_info=mlflow.pyfunc.log_model(name='pump-failure',python_model=PortablePumpModel(),
            artifacts={'portable':selected['portable_path']},code_paths=[str(Path(__file__).parent)],
            signature=infer_signature(features,predicted),input_example=features.iloc[:2],
            pip_requirements=['mlflow==3.17.2','numpy==2.5.4','pandas==2.3.3'])
        version=mlflow.register_model(model_info.model_uri,'iot-pump-failure-v1')
        client.set_model_version_tag('iot-pump-failure-v1',version.version,'protocol_sha256',PROTOCOL_SHA256)
        client.set_model_version_tag('iot-pump-failure-v1',version.version,'production','false')
    registered=client.get_model_version('iot-pump-failure-v1',version.version)
    if registered.run_id!=run_id:raise ValueError('Registry alias resolves to a different source run')
    restored=mlflow.pyfunc.load_model('models:/iot-pump-failure-v1/'+str(version.version)).predict(features)
    if not np.allclose(restored.risk_score.to_numpy(),sample.risk_score.to_numpy(),rtol=0,atol=1e-12):
        raise ValueError('Reloaded registry model differs from actual native Spark prediction')
    # Read actual remotely stored training/validation/test bytes back and bind each file.
    with tempfile.TemporaryDirectory() as temp:
        downloaded=Path(client.download_artifacts(run_id,'data',temp))
        for name,item in partitions['split_paths'].items():
            if inventory(downloaded/name)!=inventory(item):raise ValueError('MLflow stored split data differs byte-for-byte')
    client.set_registered_model_alias('iot-pump-failure-v1','latest',version.version)
    client.set_model_version_tag('iot-pump-failure-v1',version.version,'latest','true')
    if client.get_model_version_by_alias('iot-pump-failure-v1','latest').run_id!=run_id:
        raise ValueError('Latest alias changed while validating this version')
    save(args.output,{'passed':True,'run_id':run_id,'model_name':'iot-pump-failure-v1','version':registered.version,
        'alias':'latest','production':False,'model_uri':model_info.model_uri,'data_version':partitions['native_report_sha256'],
        'dataset_files':{n:inventory(p) for n,p in partitions['split_paths'].items()},
        'all_stored_split_data_readback_exact':True,'registry_model_native_spark_probe_rows':len(sample),
        'max_abs_probability_difference':float(np.max(np.abs(restored.risk_score.to_numpy()-sample.risk_score.to_numpy()))),
        'evaluation_sha256':digest(args.evaluation),'protocol_sha256':PROTOCOL_SHA256,'code_sha256':digest(Path(__file__)),
        'scope':'Real MLflow database-backed registry/alias, actual split Parquet/model artifacts uploaded and read back; synthetic medium cohort, not production KServe or100GB ML.'})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evaluation',type=Path,required=True);parser.add_argument('--protocol',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--tracking-uri',required=True)
    parser.add_argument('--run',required=True);parser.add_argument('--code-commit',required=True)
    run(parser.parse_args())
