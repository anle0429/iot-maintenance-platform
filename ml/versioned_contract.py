"""Declare the source closure for automatic data/model persistence without including credentials or runtime state."""
SOURCE_FILES=[
    'ml/automatic_versions.py','ml/versioned_contract.py','ml/kfp_versioned.py','ml/kfp_notebook.py',
    'ml/notebook_load.py','ml/notebook_load_component.py','ml/snapshot.py','ml/notebook_runtime.py',
    'ml/notebook_validate.py','ml/notebook_validate_component.py','ml/distributed_submit.py',
    'ml/distributed_job.py','ml/distributed_evidence.py','ml/kfp_cluster.py','ml/kfp_training.py',
    'ml/kfp_artifacts.py','ml/cluster_training_job.py','ml/training_job.py','ml/training_contract.py',
    'ml/protocol.py','ml/protocols/medium-60d-v1.json','ml/canonical_dataset.py','ml/data_versions.py',
    'ml/mlflow_data_versions.py','ml/model_data_link.py','ml/register_incremental_model.py','ml/portable_model.py',
    'scripts/version-feast-dataset.py','scripts/run-training-notebook.py','scripts/run-versioned-training.py',
    'scripts/create-versioned-source-binding.py','notebooks/01_failure_training.ipynb',
    'infra/kfp/spark-executor.yaml','infra/kfp/versioning-volumes.yaml',
    'infra/ml-versioning/Feast.Dockerfile','infra/ml-versioning/Spark.Dockerfile',
    'infra/ml-versioning/requirements-feast.lock','infra/ml-versioning/requirements-spark.lock']
