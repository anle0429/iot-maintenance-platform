"""Keep five notebook-equivalent ML tasks, automatically versioning each pull and registering its evaluated model."""
from kfp import compiler,dsl,kubernetes
from kfp.dsl import ContainerSpec,Dataset,Artifact,Input,Output
from ml.kfp_cluster import configure,cluster_split
from ml.kfp_notebook import distributed_train,validate_saved_model


@dsl.container_component
def versioned_load(workspace:str,source_binding:str,cohort:Output[Dataset]):
    """Treat a failed incremental publication as a failed load, preventing unversioned training."""
    return ContainerSpec(image='iot-coursework-feast-versioned:0.67.0-3.17.2',command=['python','-m','ml.automatic_versions'],args=[
        '--step','load','--workspace',workspace,'--output',cohort.path,'--source-binding',source_binding,
        '--protocol','/workspace/ml/protocols/medium-60d-v1.json','--state','/workspace/data/feast/batch-medium-27',
        '--batch-report','/workspace/outputs/feast-batch-27/batch/report.json',
        '--artifacts','/workspace/data/feature-datasets/feature-medium-27-v2/artifacts',
        '--feature-report','/workspace/outputs/feature-medium-27-v2/feature-report.json',
        '--executor-input',dsl.PIPELINE_TASK_EXECUTOR_INPUT_PLACEHOLDER])


@dsl.container_component
def registered_evaluate(workspace:str,source_binding:str,selection:Input[Artifact],verification:Input[Artifact],evaluation:Output[Artifact]):
    """Require both trained weights and their successful reload validation before evaluation and candidate registration."""
    return ContainerSpec(image='iot-coursework-spark-registry:3.5.9-3.17.2',command=['python3','-m','ml.automatic_versions'],args=[
        '--step','evaluate','--workspace',workspace,'--source-binding',source_binding,
        '--selection',selection.path,'--verification',verification.path,'--output',evaluation.path,
        '--protocol','/workspace/ml/protocols/medium-60d-v1.json','--executor-input',dsl.PIPELINE_TASK_EXECUTOR_INPUT_PLACEHOLDER])


@dsl.pipeline(name='iot-versioned-distributed-training-v1',description='Five ML stages, fresh Feast-to-incremental-data publication and model-only candidate registration.')
def versioned_pipeline(workspace:str='/ml-data/versioned-training',source_binding:str='/workspace/data/runtime/ml-versioning/source-snapshot.json'):
    """Preserve the frozen ML protocol and replace only persistence hooks around existing load/evaluation kernels."""
    load=configure(versioned_load(workspace=workspace,source_binding=source_binding),10001)
    kubernetes.mount_pvc(load,'iot-data-cas','/data-cas')
    split=configure(cluster_split(workspace=workspace,cohort=load.outputs['cohort']),185)
    train=configure(distributed_train(workspace=workspace,partitions=split.outputs['partitions']),185)
    kubernetes.use_field_path_as_env(train,env_name='IOT_DRIVER_POD',field_path='metadata.name')
    kubernetes.use_field_path_as_env(train,env_name='IOT_DRIVER_IP',field_path='status.podIP')
    validation=configure(validate_saved_model(selection=train.outputs['selection']),185)
    configure(registered_evaluate(workspace=workspace,source_binding=source_binding,selection=train.outputs['selection'],
        verification=validation.outputs['verification']),185)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);args=parser.parse_args()
    compiler.Compiler().compile(versioned_pipeline,args.output)
