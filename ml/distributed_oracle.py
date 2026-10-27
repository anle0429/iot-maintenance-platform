"""Exercise the unchanged 360-row time-cut/model oracle through the actual two-executor Kubernetes scheduler."""
import argparse
from pathlib import Path
from types import SimpleNamespace
from pyspark.sql import SparkSession
from ml.training_oracles import run


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['workspace','output','protocol']:parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();spark=SparkSession.builder.appName('iot-distributed-known-answer-oracle').getOrCreate()
    if not spark.sparkContext.master.startswith('k8s://'):raise ValueError('Oracle requires real Kubernetes executors')
    spark.sparkContext.setJobGroup('iot-distributed-fit','Known-answer360-row distributed LR fixture, not IoT quality metrics',interruptOnCancel=True)
    run(SimpleNamespace(workspace=args.workspace/'algorithm',output=args.output,protocol=args.protocol))
