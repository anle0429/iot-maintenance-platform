# Guarded full-source Spark runner

The Linux runner verifies the retained source and actual session/resources before submitting the large Spark workload. It keeps source code and inputs read-only, places scratch according to the checked disk budget and records image/container/command identities.

Two checksum observations, completed Spark application state and exact output validation bind the result. Long-running progress and resource records are retained separately from the final success receipt. A process exit or partial Parquet directory is not enough to mark ingestion complete.

The admitted full-source workload uses HDD scratch where the SSD budget is insufficient. Pilot speed and remaining auto-STOP time are checked with reserve rather than bypassing a failed guard. This runner does not automatically execute the full feature/ML pipeline or turn the pilot Airflow DAG into a 100 GB orchestration run.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```bash
data/runtime/scale-venv/bin/python scripts/run-large-ingest.py \
  --run-name my-hdd-pilot --source data/source/scale-intermediate-26 \
  --receipt outputs/scale-intermediate-26/receipt.json --partitions 128 \
  --deadline-utc "$VM_STOP_UTC" --base-commit "$RELEASE_COMMIT"
```

## Implementation and evidence

- [spark.apache.org / configuration.html](https://spark.apache.org/docs/3.5.9/configuration.html)
- [spark.apache.org / sql-performance-tuning.html](https://spark.apache.org/docs/3.5.9/sql-performance-tuning.html)
- [Recorded artifact: large-ingest-runner/ingest-report.json](evidence/large-ingest-runner/ingest-report.json)
- [Recorded artifact: large-ingest-runner/verification.json](evidence/large-ingest-runner/verification.json)
- [Recorded artifact: large-ingest-runner/_RUN_SUCCESS.json](evidence/large-ingest-runner/_RUN_SUCCESS.json)
- [Recorded artifact: large-ingest-runner/lakehouse-_RUN_SUCCESS.json](evidence/large-ingest-runner/lakehouse-_RUN_SUCCESS.json)
- [Recorded artifact: large-ingest-runner/container-limits.json](evidence/large-ingest-runner/container-limits.json)
- [Recorded artifact: large-ingest-runner/preflight.json](evidence/large-ingest-runner/preflight.json)
- [Recorded artifact: large-ingest-runner/calibration-v3/resource-samples.json](evidence/large-ingest-runner/calibration-v3/resource-samples.json)
- [Recorded artifact: large-ingest-runner/admission/plan.json](evidence/large-ingest-runner/admission/plan.json)
- [Recorded artifact: large-ingest-runner/admission/rejected.json](evidence/large-ingest-runner/admission/rejected.json)
- [Recorded artifact: large-ingest-runner/admission/next-session-estimate.json](evidence/large-ingest-runner/admission/next-session-estimate.json)
- [cloud.google.com / general-purpose?hl=en](https://cloud.google.com/products/compute/pricing/general-purpose?hl=en)
- [cloud.google.com / disks-image-pricing](https://cloud.google.com/compute/disks-image-pricing)
- [cloud.google.com / pricing](https://cloud.google.com/vpc/pricing)
- [Recorded artifact: large-ingest-runner/release.json](evidence/large-ingest-runner/release.json)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](demo-walkthrough.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/large-ingest-runner.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
