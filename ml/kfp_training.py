"""Compile a genuine four-component KFP DAG with native Feast load, temporal split, train and evaluation."""

from kfp import compiler,dsl
from kfp.dsl import Dataset,Input,Output,Artifact,ContainerSpec


@dsl.container_component
def load_feast(workspace:str,cohort:Output[Dataset]):
    """Retrieve all eligible entities through the pre-registered native Feast service."""
    return ContainerSpec(image='iot-coursework-feast:0.67.0',command=['python','-m','ml.training_load'],args=[
        '--workspace',workspace,'--protocol','/workspace/ml/protocols/medium-60d-v1.json',
        '--state','/workspace/data/feast/batch-medium-27','--batch-report','/workspace/outputs/feast-batch-27/batch/report.json',
        '--artifacts','/workspace/data/feature-datasets/feature-medium-27-v2/artifacts',
        '--feature-report','/workspace/outputs/feature-medium-27-v2/feature-report.json','--output',cohort.path,
        '--executor-input',dsl.PIPELINE_TASK_EXECUTOR_INPUT_PLACEHOLDER])


def spark_spec(step,workspace,input_path,output_path):
    """Keep identical bounded Spark settings in all three isolated JVM components."""
    return ContainerSpec(image='iot-coursework-spark:3.5.9',
        command=['/opt/spark/bin/spark-submit','--master','local[2]','--driver-memory','6g',
            '--conf','spark.driver.maxResultSize=128m','--conf','spark.local.dir=/ml-scratch',
            '/workspace/ml/training_job.py'],args=['--step',step,'--input',input_path,'--output',output_path,
            '--workspace',workspace,'--protocol','/workspace/ml/protocols/medium-60d-v1.json',
            '--executor-input',dsl.PIPELINE_TASK_EXECUTOR_INPUT_PLACEHOLDER])


@dsl.container_component
def split_time(workspace:str,cohort:Input[Dataset],partitions:Output[Dataset]):
    """Re-derive chronological partitions, label purge, identity and minimum class/episode support."""
    return spark_spec('split',workspace,cohort.path,partitions.path)


@dsl.container_component
def train_model(workspace:str,partitions:Input[Dataset],selection:Output[Artifact]):
    """Fit on train and select the threshold using validation; never open holdout Parquet rows."""
    return spark_spec('train',workspace,partitions.path,selection.path)


@dsl.container_component
def evaluate_model(workspace:str,selection:Input[Artifact],evaluation:Output[Artifact]):
    """Require the sealed selection before a single holdout evaluation and portable-model check."""
    return spark_spec('evaluate',workspace,selection.path,evaluation.path)


@dsl.pipeline(name='iot-failure-training-v1',description='Frozen60-day protocol, native Feast load and isolated train/holdout components.')
def training_pipeline(workspace:str='/ml-data/training'):
    """Keep data/selection dependencies explicit so no evaluation can race threshold selection."""
    load=load_feast(workspace=workspace)
    split=split_time(workspace=workspace,cohort=load.outputs['cohort'])
    train=train_model(workspace=workspace,partitions=split.outputs['partitions'])
    evaluation=evaluate_model(workspace=workspace,selection=train.outputs['selection'])
    for task in [load,split,train,evaluation]:
        task.set_caching_options(False).set_cpu_limit('2').set_memory_limit('10Gi')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    args=parser.parse_args();compiler.Compiler().compile(pipeline_func=training_pipeline,package_path=args.output)
