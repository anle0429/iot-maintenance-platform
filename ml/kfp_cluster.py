"""Compile the existing frozen four-stage training DAG for the private single-node KFP backend."""
from kfp import compiler,dsl,kubernetes
from kfp.dsl import ContainerSpec,Dataset,Input,Output,Artifact
from ml.kfp_training import load_feast,spark_spec


def configure(task,uid):
    """Mount bounded existing-VM paths and use image-specific unprivileged users with a shared group."""
    for claim,path in [('iot-ml-code','/workspace'),('iot-ml-workspace','/ml-data'),('iot-ml-scratch','/ml-scratch')]:
        kubernetes.mount_pvc(task,claim,path)
    kubernetes.set_image_pull_policy(task,'Never')
    kubernetes.set_security_context(task,run_as_user=uid,run_as_group=10001,run_as_non_root=True)
    task.set_caching_options(False).set_cpu_limit('2').set_memory_limit('10Gi')
    task.set_cpu_request('500m').set_memory_request('2Gi')
    for key,value in {'HOME':'/tmp','PYTHONPATH':'/workspace','FEAST_USAGE':'False',
        'IOT_FEAST_REDIS':'online:6379','IOT_FEAST_TEMP':'/ml-scratch/offline',
        'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','SPARK_LOCAL_IP':'127.0.0.1'}.items():task.set_env_variable(key,value)
    return task


def cluster_spec(step,workspace,input_path,output_path):
    """Reuse the exact Spark command/settings and add only persistent descriptor transport."""
    spec=spark_spec(step,workspace,input_path,output_path)
    spec.command[-1]='/workspace/ml/cluster_training_job.py'
    return spec


@dsl.container_component
def cluster_split(workspace:str,cohort:Input[Dataset],partitions:Output[Dataset]):
    """Split the same native cohort and persist the descriptor consumed by the training pod."""
    return cluster_spec('split',workspace,cohort.path,partitions.path)


@dsl.container_component
def cluster_train(workspace:str,partitions:Input[Dataset],selection:Output[Artifact]):
    """Run unchanged train/validation selection with a descriptor path available to evaluation."""
    return cluster_spec('train',workspace,partitions.path,selection.path)


@dsl.container_component
def cluster_evaluate(workspace:str,selection:Input[Artifact],evaluation:Output[Artifact]):
    """Require the preserved split bytes before the unchanged holdout evaluation."""
    return cluster_spec('evaluate',workspace,selection.path,evaluation.path)


@dsl.pipeline(name='iot-failure-training-cluster-v1',description='Same sealed protocol and native Feast/train/evaluate components on the real private KFP cluster.')
def training_pipeline(workspace:str='/ml-data/training'):
    """Reproduce all four real steps without changing the split, feature allowlist or threshold grid."""
    load=configure(load_feast(workspace=workspace),10001)
    split=configure(cluster_split(workspace=workspace,cohort=load.outputs['cohort']),185)
    train=configure(cluster_train(workspace=workspace,partitions=split.outputs['partitions']),185)
    configure(cluster_evaluate(workspace=workspace,selection=train.outputs['selection']),185)


@dsl.container_component
def transport_write(output:Output[Dataset]):
    """Publish a known-answer typed artifact from the Feast image user."""
    return ContainerSpec(image='iot-coursework-feast:0.67.0',command=['python','-m','ml.kfp_artifacts'],args=[
        '--output',output.path,'--executor-input',dsl.PIPELINE_TASK_EXECUTOR_INPUT_PLACEHOLDER,'--name','output'])


@dsl.container_component
def transport_read(source:Input[Dataset],output:Output[Dataset]):
    """Read that actual cluster artifact from the Spark image user before expensive training."""
    return ContainerSpec(image='iot-coursework-spark:3.5.9',command=['python3','-m','ml.kfp_artifacts'],args=[
        '--input',source.path,'--output',output.path,'--executor-input',dsl.PIPELINE_TASK_EXECUTOR_INPUT_PLACEHOLDER,'--name','output'])


@dsl.pipeline(name='iot-cluster-artifact-oracle-v1')
def transport_pipeline():
    """Exercise actual object-store transport and UIDs10001/185; this is not the training milestone."""
    first=configure(transport_write(),10001)
    configure(transport_read(source=first.outputs['output']),185)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    parser.add_argument('--oracle',action='store_true');args=parser.parse_args()
    compiler.Compiler().compile(pipeline_func=transport_pipeline if args.oracle else training_pipeline,package_path=args.output)
