# The 60-day feature and label dataset

The Spark feature job builds five-minute snapshots from the verified Silver source under the fixed `medium-60d-v1` protocol. It reuses the 200-machine, 60-day source rather than regenerating data or treating the 100 GB ingest as a feature benchmark.

The recorded `feature-medium-27-v2` run produced 3,456,000 snapshots and 3,132,184 eligible training-table rows after censoring, feature-quality exclusions and purging. Event and arrival time are simulated; `created` is the actual build clock.

| Partition | Eligible rows | Positive | Negative | Distinct failure episodes |
|---|---:|---:|---:|---:|
| Train | 2,195,668 | 93,603 | 2,102,065 | 7,870 |
| Validation | 468,132 | 19,828 | 448,304 | 1,677 |
| Test | 468,384 | 19,957 | 448,427 | 1,687 |

Eight features, separate labels and exclusion/audit records are written. The job reports exclusions in a fixed priority order to avoid double counting. Known-answer checks cover arrivals, horizon boundaries and split purging; multiset readback and input/output hashes bind the full result.

The native run used the existing 8-vCPU/64-GiB VM, with a 6-CPU/28-GiB container, 20-GiB Spark heap, 128 shuffle partitions and HDD scratch. Those limits describe this run, not the unrelated full-source ingestion. Passing cohort-support gates establishes that evaluation is possible; it does not by itself establish model quality.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```bash
data/runtime/scale-venv/bin/python scripts/run-feature-dataset.py \
  --run-name feature-medium-check-01 --deadline-utc "$ACTUAL_STOP_DEADLINE_UTC"
```

## Implementation and evidence

- [Source: pipelines/feature_dataset.py](../pipelines/feature_dataset.py)
- [Source: ml/protocol.py](../ml/protocol.py)
- [Recorded artifact: feature-medium-60d/preflight.json](evidence/feature-medium-60d/preflight.json)
- [Recorded artifact: feature-medium-60d/container-limits.json](evidence/feature-medium-60d/container-limits.json)
- [Recorded artifact: feature-medium-60d/feature-dataset-vm-unit-tests.txt](evidence/feature-medium-60d/feature-dataset-vm-unit-tests.txt)
- [Recorded artifact: feature-medium-60d/input-binding.json](evidence/feature-medium-60d/input-binding.json)
- [Recorded artifact: feature-medium-60d/spark-metrics.json](evidence/feature-medium-60d/spark-metrics.json)
- [Recorded artifact: feature-medium-60d/feature-report.json](evidence/feature-medium-60d/feature-report.json)
- [Recorded artifact: feature-medium-60d/verification.json](evidence/feature-medium-60d/verification.json)
- [Recorded artifact: feature-medium-60d/_RUN_SUCCESS.json](evidence/feature-medium-60d/_RUN_SUCCESS.json)
- [Recorded artifact: feature-medium-60d/features-_FEATURES_SUCCESS.json](evidence/feature-medium-60d/features-_FEATURES_SUCCESS.json)
- [Recorded artifact: feature-medium-60d/feature-dataset-code-v3-deployed.json](evidence/feature-medium-60d/feature-dataset-code-v3-deployed.json)
- [Source: scripts/run-feature-dataset.py](../scripts/run-feature-dataset.py)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/medium-feature-dataset.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
