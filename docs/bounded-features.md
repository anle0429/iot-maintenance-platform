# Bounded temporal joins for feature engineering

A machine snapshot at t needs only readings in `(t−10 minutes, t]`. Joining on machine ID alone can consider an entire history before filtering. The bucketed implementation narrows candidates and then reapplies the exact temporal and availability predicates.

| Calculation | Bucket width | Candidate buckets | Exact rule retained |
|---|---:|---|---|
| Past telemetry | 600 seconds | Current and previous | `t−600s < event_ts <= t`, arrival no later than t |
| Recorded failures | 86,400 seconds | Current and previous | Past 24 hours and recorded by t |
| Future label/intervention | 3,600 seconds | Current and next | `t < start_ts <= t+1h` |

Each event belongs to one bucket, and each snapshot expands to two distinct buckets. A valid event therefore contributes once. Empty left joins collapse back to one snapshot: count zero, mean/max NULL and the contract-defined standard deviation. Deduplicated inputs and unique machine/snapshot keys are prerequisites.

The independent Python oracle compares range and bucketed implementations on exact boundaries, one-microsecond offsets, midnight and pre-epoch times, unavailable arrivals/failures, missing readings, stopped machines and censored horizons. The isolated native fixture passed eight checks over nine snapshots, 168 readings, 192 failure rows and four maintenance rows. Its 34.059-second duration includes startup and both strategies; it is a correctness fixture, not a speed benchmark.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```bash
/opt/spark/bin/spark-submit --master 'local[2]' --driver-memory 2g \
  --conf spark.sql.shuffle.partitions=4 --conf spark.default.parallelism=2 \
  --conf spark.sql.adaptive.enabled=false \
  /workspace/pipelines/feature_checks.py --output /workspace/outputs/feature-check-local-01
```

## Implementation and evidence

- [Source: pipelines/bounded_features.py](../pipelines/bounded_features.py)
- [spark.apache.org / pyspark.sql.functions.unix_micros.html](https://spark.apache.org/docs/3.5.9/api/python/reference/pyspark.sql/api/pyspark.sql.functions.unix_micros.html)
- [Source: pipelines/feature_checks.py](../pipelines/feature_checks.py)
- [Recorded artifact: feature-buckets/report.json](evidence/feature-buckets/report.json)
- [Recorded artifact: feature-buckets/verification.json](evidence/feature-buckets/verification.json)
- [Recorded artifact: feature-buckets/range_features-plan.txt](evidence/feature-buckets/range_features-plan.txt)
- [Recorded artifact: feature-buckets/bucketed_features-plan.txt](evidence/feature-buckets/bucketed_features-plan.txt)
- [Recorded artifact: feature-buckets/range_labels-plan.txt](evidence/feature-buckets/range_labels-plan.txt)
- [Recorded artifact: feature-buckets/bucketed_labels-plan.txt](evidence/feature-buckets/bucketed_labels-plan.txt)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](demo-walkthrough.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/bounded-features.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
