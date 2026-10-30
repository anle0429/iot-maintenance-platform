# Automatic incremental data and model versioning

Notebook and five-task KFP runs automatically register a dataset version and publish model artifacts after a fresh Feast pull. The recorded complete runs created model candidates 4 and 5 under the fixed protocol.

Content-addressed parts and manifests describe the training dataset. If the retrieved data is unchanged, an additional run reuses existing blobs instead of storing a second full copy; the recorded unchanged pull uploaded zero new blob bytes. A complete restore is compared with the canonical data, and model lineage points to that version.

The process does not select a new split or threshold after seeing holdout results. Model registration is not serving promotion. Later training/serving CI uses its own source bindings and model 6 pin. Registry readbacks, data manifests and native task completion provide the versioning evidence.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```bash
sudo python3 scripts/configure-kfp-registry.py \
  --run registry-demo-new --data-run "$DATA_RUN_ID"
sudo data/runtime/kfp-venv/bin/python scripts/run-versioned-training.py \
  --run versioned-training-demo-new --deadline-utc "$DEADLINE_UTC" \
  --source-binding data/runtime/ml-versioning/source-snapshot.json \
  --oracle outputs/distributed-oracle/distributed-smoke-27-v2/verification.json
```

```bash
sudo data/runtime/notebook-venv/bin/python scripts/run-training-notebook.py \
  --run versioned-notebook-demo-new --deadline-utc "$DEADLINE_UTC"
```

## Implementation and evidence

- [Source: ml/kfp_versioned.py](../ml/kfp_versioned.py)
- [Source: notebooks/01_failure_training.ipynb](../notebooks/01_failure_training.ipynb)
- [Source: ml/automatic_versions.py](../ml/automatic_versions.py)
- [Source: ml/canonical_dataset.py](../ml/canonical_dataset.py)
- [Source: ml/data_versions.py](../ml/data_versions.py)
- [Source: ml/model_data_link.py](../ml/model_data_link.py)
- [Source: ml/register_incremental_model.py](../ml/register_incremental_model.py)
- [Source: infra/ml-versioning/Feast.Dockerfile](../infra/ml-versioning/Feast.Dockerfile)
- [Source: infra/ml-versioning/Spark.Dockerfile](../infra/ml-versioning/Spark.Dockerfile)
- [Source: scripts/configure-kfp-registry.py](../scripts/configure-kfp-registry.py)
- [Source: tests/test_data_versions.py](../tests/test_data_versions.py)
- [Source: scripts/create-versioned-source-binding.py](../scripts/create-versioned-source-binding.py)
- [Recorded artifact: automatic-versioning/notebook/execution/execution.json](evidence/automatic-versioning/notebook/execution/execution.json)
- [Recorded artifact: automatic-versioning/kfp/controller/verification.json](evidence/automatic-versioning/kfp/controller/verification.json)
- [Recorded artifact: automatic-versioning/notebook/dataset/lineage/automatic-data.json](evidence/automatic-versioning/notebook/dataset/lineage/automatic-data.json)
- [Recorded artifact: automatic-versioning/kfp/dataset/lineage/automatic-data.json](evidence/automatic-versioning/kfp/dataset/lineage/automatic-data.json)
- [Recorded artifact: automatic-versioning/notebook/dataset/lineage/registration.json](evidence/automatic-versioning/notebook/dataset/lineage/registration.json)
- [Recorded artifact: automatic-versioning/kfp/dataset/lineage/registration.json](evidence/automatic-versioning/kfp/dataset/lineage/registration.json)
- [Recorded artifact: automatic-versioning/model-run-readback/verification.json](evidence/automatic-versioning/model-run-readback/verification.json)
- [Recorded artifact: automatic-versioning/kfp/controller/distributed-verification.json](evidence/automatic-versioning/kfp/controller/distributed-verification.json)
- [Recorded artifact: automatic-versioning/capture/two-running-executors.json](evidence/automatic-versioning/capture/two-running-executors.json)
- [Recorded artifact: automatic-versioning/typed-artifacts/verification.json](evidence/automatic-versioning/typed-artifacts/verification.json)
- [Recorded artifact: automatic-versioning/source/source-snapshot.json](evidence/automatic-versioning/source/source-snapshot.json)
- [Recorded artifact: automatic-versioning/preparation/helper-readback/verification.json](evidence/automatic-versioning/preparation/helper-readback/verification.json)
- [Recorded artifact: automatic-versioning/preparation/final-images/sdk-source.json](evidence/automatic-versioning/preparation/final-images/sdk-source.json)
- [Recorded artifact: automatic-versioning/preparation/registry-route/verification.json](evidence/automatic-versioning/preparation/registry-route/verification.json)
- [Recorded artifact: automatic-versioning/preparation/notebook-permissions/verification.json](evidence/automatic-versioning/preparation/notebook-permissions/verification.json)
- [Source: ml/notebook_runtime.py](../ml/notebook_runtime.py)
- [Recorded artifact: automatic-versioning/sharing/repair/verification.json](evidence/automatic-versioning/sharing/repair/verification.json)
- [Recorded artifact: automatic-versioning/sharing/source/source-snapshot.json](evidence/automatic-versioning/sharing/source/source-snapshot.json)
- [Recorded artifact: automatic-versioning/capture/manifest.json](evidence/automatic-versioning/capture/manifest.json)
- [Recorded artifact: automatic-versioning/sharing/capture/manifest.json](evidence/automatic-versioning/sharing/capture/manifest.json)
- [Recorded artifact: automatic-versioning/typed-artifacts/capture/manifest.json](evidence/automatic-versioning/typed-artifacts/capture/manifest.json)
- [Recorded artifact: automatic-versioning/model-run-readback/capture/manifest.json](evidence/automatic-versioning/model-run-readback/capture/manifest.json)
- [Recorded artifact: automatic-versioning/screenshots/capture.json](evidence/automatic-versioning/screenshots/capture.json)

## Recorded interface views

These are original captures from the runs described above. Their timestamps, model identities and visible states belong to those runs.

![Recorded five versioned tasks succeeded](evidence/automatic-versioning/screenshots/five-versioned-tasks-succeeded.jpg)

![Recorded notebook automatic data zero new bytes](evidence/automatic-versioning/screenshots/notebook-automatic-data-zero-new-bytes.jpg)

![Recorded mlflow model5 candidate data version](evidence/automatic-versioning/screenshots/mlflow-model5-candidate-data-version.jpg)


## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/automatic-ml-versioning.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
