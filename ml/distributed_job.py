"""Run the frozen train kernel with genuine Kubernetes executors and mark its Spark event-log jobs."""
import runpy,sys
from pyspark.sql import SparkSession
from ml.cluster_training_job import persistent_input


if __name__=='__main__':
    sys.argv=[sys.argv[0],*persistent_input(sys.argv[1:])]
    spark=SparkSession.builder.appName('iot-distributed-training').getOrCreate()
    if not spark.sparkContext.master.startswith('k8s://'):
        raise ValueError('Distributed training requires the Kubernetes scheduler, not local threads')
    spark.sparkContext.setJobGroup('iot-distributed-fit','Frozen train-only StandardScaler and LogisticRegression, then validation selection',interruptOnCancel=True)
    runpy.run_module('ml.training_job',run_name='__main__')
