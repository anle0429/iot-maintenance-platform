# ML lifecycle

The model estimates whether an operating simulated pump begins a failure in the next hour. Data eligibility and temporal splits are fixed by protocol before evaluation. Saved portable parameters and model/source pins make inference independently reproducible.

| Responsibility | Main entry | Report |
|---|---|---|
| Eligibility and split policy | [protocol.py](protocol.py), [training_contract.py](training_contract.py), [fixed protocol](protocols/medium-60d-v1.json) | [Evaluation protocol](../docs/edai-1/ml_evaluation_protocol.md) |
| Notebook/pipeline parity | [notebook_runtime.py](notebook_runtime.py), [kfp_notebook.py](kfp_notebook.py) | [Executed notebook](../docs/edai-1/training_notebook.md) |
| Distributed execution | [distributed_job.py](distributed_job.py), [distributed_oracle.py](distributed_oracle.py) | [Native distributed run](../docs/edai-1/distributed_training.md) |
| Incremental versions | [data_versions.py](data_versions.py), [automatic_versions.py](automatic_versions.py) | [Versioning](../docs/edai-1/automatic_ml_versioning.md) |
| Served artifact | [portable_model.py](portable_model.py), [serving_release.py](serving_release.py) | [Serving](../docs/edai-1/model_serving.md), [five core classes](../docs/edai-1/ml_class_design.md) |
| Drift and retraining | [drift_monitoring.py](drift_monitoring.py), [drift_eventing.py](drift_eventing.py), [drift_retrain.py](drift_retrain.py) | [Monitored retraining](../docs/edai-1/drift_retraining.md) |

Model development uses a separate 60-day source, not the 100 GB Bronze/Silver workload. Scores are uncalibrated; time-aware synthetic-data metrics do not certify industrial failure probabilities.
