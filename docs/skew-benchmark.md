# Spark join-skew benchmark

The benchmark compares baseline and optimized versions of the same factory-policy join on 17.28 millionSilver rows. Three paired runs check equal results and preserve the same source, machine and workload.

Broadcasting the small factory-policy relation reduced the recorded median from 15.339 to 8.577 seconds. Query plans, task/partition metrics and genuine Spark UI captures explain the changed execution strategy.

The synthetic factory distribution is skewed by construction, but that alone is not optimization evidence. Only this measured query/workload supports the comparison. The result does not establish the same speedup for all joins,100 GB feature engineering or an otherwise different cluster.

## Before and after on the same input

The input has 17,280,000 unique readings: F-01 has 12,096,000 (70%), while F-02 and F-03 have 2,592,000 each. A factory-key partition diagnostic placed all 12,096,000 F-01 rows in partition 22. The baseline sorts/shuffles the large relation for a `SortMergeJoin`; the optimized query broadcasts the **three-row policy relation** and uses `BroadcastHashJoin`. It does not rebalance factories or remove data.

| Same-input pair | Baseline, seconds | Broadcast, seconds |
|---|---:|---:|
| 1 | 18.243 | 9.115 |
| 2 | 15.338 | 8.444 |
| 3 | 15.339 | 8.577 |
| Median | 15.339 | 8.577 |

The median reduction is 44.1% (1.788× observed speedup). Each execution returns the same 4,320 rows and SHA-256 `7709e19ad539bcff7eeab3c17925c9a18d406dd9f9b69af0c01822d9ca41f877`. Across each run's recorded jobs, shuffle-read bytes change from 302,603,711 to 1,076,691; both strategies have 47 task attempts, zero failed attempts and zero disk spill. These are [event-log measurements](evidence/skew-benchmark/task-metrics.json), not estimates from the picture.

![Paired elapsed times on the same immutable Silver input](images/skew-before-after.png)

The plot derives from the original receipt. The following are **original Spark UI screenshots**, with the before and after identified explicitly:

| Before: sort/merge strategy and hot tasks | After: broadcast strategy |
|---|---|
| ![Baseline physical plan](evidence/skew-benchmark/screenshots/baseline-sql-18.png) | ![Optimized physical plan](evidence/skew-benchmark/screenshots/broadcast-sql-19.png) |
| ![Baseline hot tasks](evidence/skew-benchmark/screenshots/baseline-hot-tasks.png) | ![Broadcast stage tasks](evidence/skew-benchmark/screenshots/broadcast-stage-85.png) |

Both settings disable AQE and automatic broadcasting to isolate the explicit join change; shuffle partitions remain 32. OS caches and other VM services were not isolated. These original measurements came from a standalone analytical job. The same query now also has a separately recorded [native Airflow execution](#airflow-benchmark-execution); the original result above has not been replaced.

## Airflow benchmark execution

The new [iot_data_benchmarks DAG](../airflow/dags/iot_data_benchmarks.py) runs source/resource admission, schema/dedup pairs and their validation before executing `skew_pairs → validate_skew`. Both skew tasks occupy the existing single-slot `spark_jobs` pool. [The adapter](../pipelines/benchmark_airflow.py) calls the unchanged `skew_job.py` in a real 6-CPU / 28-GiB Spark container, with a 20-GiB driver heap and 32 shuffle partitions.

Run `bench-paired-20261101a` completed all five tasks on 1 November, after a separate successful pilot. Its skew application is `local-1793565724983`. The [actual Airflow snapshot](evidence/batch-benchmarks/final/airflow.json), [native benchmark report](evidence/batch-benchmarks/final/skew.json) and [validation receipt](evidence/batch-benchmarks/final/skew-verification.json) identify the execution. Original failed pilots remain in the same evidence capsule.

![Actual Airflow benchmark tasks, including skew and metrics validation](evidence/batch-benchmarks/screenshots/airflow-final-graph.jpg)

| New Airflow pair | Sort-merge baseline, seconds | Broadcast, seconds |
|---|---:|---:|
| 1 | 16.807852 | 9.215180 |
| 2 | 14.258209 | 8.507135 |
| 3 | 14.249135 | 9.169817 |
| Median | 14.258209 | 9.169817 |

The median is 35.7% lower (1.555× baseline/candidate ratio) for this new execution. All six complete results again contain the same 4,320 rows and the same SHA-256 as the original experiment. The source remains 17,280,000 Silver rows with the same 70% / 15% / 15% factory distribution. [Source bindings](evidence/batch-benchmarks/final/input-spec.json) and complete event logs support independent readback; the old and new medians describe separate executions with uncontrolled OS caches and shared VM services.

<details>
<summary>Native final Spark plans: policy SortMergeJoin versus BroadcastHashJoin</summary>

| Baseline: query 18 | Broadcast: query 19 |
|---|---|
| ![Native final policy sort-merge plan](evidence/batch-benchmarks/screenshots/skew-baseline-full.jpg) | ![Native final policy broadcast plan](evidence/batch-benchmarks/screenshots/skew-broadcast-full.jpg) |

</details>

Both plans also broadcast the shared 200-row machine snapshot. The changed join is the **three-row factory policy**: the baseline shuffles/sorts that join, while the candidate broadcasts it. The factory imbalance is preserved, not removed. See the [combined experiment methodology and offline verifier](batch-corrections-benchmark.md) for timing boundaries, all observations and source reproduction.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```bash
data/runtime/scale-venv/bin/python scripts/run-skew-benchmark.py \
  --run-name my-skew-check \
  --deadline-utc "$VM_STOP_UTC" --base-commit "$RELEASE_COMMIT"
```

## Implementation and evidence

- [Source: pipelines/configs/factory-risk-policies.json](../pipelines/configs/factory-risk-policies.json)
- [Recorded artifact: skew-benchmark/screenshots/baseline-hot-tasks.png](evidence/skew-benchmark/screenshots/baseline-hot-tasks.png)
- [Recorded artifact: skew-benchmark/screenshots/baseline-sql-18.png](evidence/skew-benchmark/screenshots/baseline-sql-18.png)
- [Recorded artifact: skew-benchmark/screenshots/broadcast-stage-85.png](evidence/skew-benchmark/screenshots/broadcast-stage-85.png)
- [Source: pipelines/skew_metrics.py](../pipelines/skew_metrics.py)
- [Recorded artifact: skew-benchmark/screenshots/paired-sql-runs.png](evidence/skew-benchmark/screenshots/paired-sql-runs.png)
- [Recorded artifact: skew-benchmark/benchmark.json](evidence/skew-benchmark/benchmark.json)
- [Recorded artifact: skew-benchmark/input-binding.json](evidence/skew-benchmark/input-binding.json)
- [Recorded artifact: skew-benchmark/verification.json](evidence/skew-benchmark/verification.json)
- [Recorded artifact: skew-benchmark/_RUN_SUCCESS.json](evidence/skew-benchmark/_RUN_SUCCESS.json)
- [Recorded artifact: skew-benchmark/resource-samples.json](evidence/skew-benchmark/resource-samples.json)
- [Recorded artifact: skew-benchmark/preflight.json](evidence/skew-benchmark/preflight.json)
- [Recorded artifact: skew-benchmark/screenshots.json](evidence/skew-benchmark/screenshots.json)
- [spark.apache.org / sql-performance-tuning.html](https://spark.apache.org/docs/3.5.9/sql-performance-tuning.html)
- [spark.apache.org / monitoring.html](https://spark.apache.org/docs/3.5.9/monitoring.html)

## Recorded interface views

These are original captures from the runs described above. Their timestamps, model identities and visible states belong to those runs.

![Recorded baseline stage 74](evidence/skew-benchmark/screenshots/baseline-stage-74.png)

![Recorded broadcast sql 19](evidence/skew-benchmark/screenshots/broadcast-sql-19.png)


## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/a395c4ea47b8f8e235556ab93de3d699ed9df18b/docs/skew-benchmark.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
