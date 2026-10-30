# Airflow-to-Kubeflow retraining admission

The native manual probe completed a real five-task KFP run and registered model 7 as a candidate with `production=false`. The observed input had `drift=false`; this was a manual integration test, not a naturally triggered drift alarm.

After comparison, the Airflow DAG passes a sealed report and policy to a controller. It validates report completion, metrics, reference/source and recipe before submitting KFP. A persistent, fsynced intent precedes the external call. Replay uses a completed receipt; an unresolved intent blocks another submission. A one-hour cooldown prevents immediate repeated training.

The controller measured 1,128.680 seconds; backend UI duration is a separate observation. No-drift, in-flight, replay and cooldown cases were checked. Model 6 serving was not automatically replaced, and a separate fresh materialization restored its demo eligibility.

This does not provide an exactly-once filesystem/KFP transaction. Ambiguous submission requires operator reconciliation rather than deleting the intent to force another run.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
python -m unittest tests.test_drift_retrain -v
python -S scripts/verify-drift-retraining.py
```

```json
{"interval_end_utc":"2026-10-28T05:40:00Z","retrain_probe":true}
```

## Implementation and evidence

- [Recorded artifact: drift-retraining-linux/admission/result.json](evidence/drift-retraining-linux/admission/result.json)
- [Recorded artifact: drift-retraining-linux/admission/backend-run.json](evidence/drift-retraining-linux/admission/backend-run.json)
- [Recorded artifact: drift-retraining-linux/airflow/trained-run.json](evidence/drift-retraining-linux/airflow/trained-run.json)
- [Recorded artifact: drift-retraining-linux/airflow/trained-tasks.json](evidence/drift-retraining-linux/airflow/trained-tasks.json)
- [Source: airflow/dags/iot_offline_drift.py](../airflow/dags/iot_offline_drift.py)
- [Source: feature_store/drift_retrain.py](../feature_store/drift_retrain.py)
- [Source: ml/drift_retrain.py](../ml/drift_retrain.py)
- [www.kubeflow.org / run-a-pipeline](https://www.kubeflow.org/docs/components/pipelines/user-guides/core-functions/run-a-pipeline/)
- [www.kubeflow.org / caching](https://www.kubeflow.org/docs/components/pipelines/user-guides/core-functions/caching/)
- [Recorded artifact: drift-retraining-linux/training/native/pipeline-pods.json](evidence/drift-retraining-linux/training/native/pipeline-pods.json)
- [Recorded artifact: drift-retraining-linux/training/spark-selected.jsonl](evidence/drift-retraining-linux/training/spark-selected.jsonl)
- [Recorded artifact: drift-retraining-linux/training/event-summary.json](evidence/drift-retraining-linux/training/event-summary.json)
- [Recorded artifact: drift-retraining-linux/training/lineage/automatic-data.json](evidence/drift-retraining-linux/training/lineage/automatic-data.json)
- [Recorded artifact: drift-retraining-linux/registry/model-version.json](evidence/drift-retraining-linux/registry/model-version.json)
- [Recorded artifact: drift-retraining-linux/training/lineage/registration.json](evidence/drift-retraining-linux/training/lineage/registration.json)
- [Recorded artifact: drift-retraining-linux/checks/completed-replay/verification.json](evidence/drift-retraining-linux/checks/completed-replay/verification.json)
- [Recorded artifact: drift-retraining-linux/checks/cooldown-proof/verification.json](evidence/drift-retraining-linux/checks/cooldown-proof/verification.json)
- [Recorded artifact: drift-retraining-linux/manifest.json](evidence/drift-retraining-linux/manifest.json)
- [Recorded artifact: drift-retraining-linux/native-capture.json](evidence/drift-retraining-linux/native-capture.json)

## Recorded interface views

These are original captures from the runs described above. Their timestamps, model identities and visible states belong to those runs.

![Recorded kfp retrain success detail](evidence/drift-retraining-linux/screenshots/kfp-retrain-success-detail.jpg)

![Recorded kfp retrain success graph](evidence/drift-retraining-linux/screenshots/kfp-retrain-success-graph.jpg)


## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/drift-retraining.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
