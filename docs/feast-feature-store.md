# Feast offline and online feature stores

Feast names the entity, feature schema and retrieval interfaces shared by training and serving. The recorded implementation uses entity `machine_id`, view `machine_rolling_10m` and service `iot_failure_prediction_v1`, with eight protocol-bound fields. Labels and future failures are outside the view.

Offline features are immutable Parquet; online features are Redis. The view's ten-minute event-time TTL differs from the Redis key's 3,600-second wall-clock expiry. The SDK runs inside jobs/services, not in a separate Feast HTTP server.

The native SDK fixture passed eight checks for point-in-time boundaries, corrections and arrival availability, unknown entities, older writes, replay and physical key expiry. It also demonstrates why a point-in-time join alone does not prove availability. The 60-day backfill uses `filter_by_created_timestamp=False` explicitly because `created` is build time; upstream arrival guards establish historical eligibility.

Compaction retained all 3,456,000 feature rows while changing 128 files/row groups into one file with 13 row groups. Full historical retrieval preserved 3,132,184 eligible rows and their fixed labels/splits. Source, protocol, feature and compacted-artifact hashes bind the result.

The Feast image and dependency lock are separate from Spark/Airflow. Redis runs on an internal network with AOF and no-eviction settings. The native receipts establish this workload; they do not establish full-100-GB feature retrieval or production multi-host availability.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```bash
sudo install -d -o10001 -g10001 data/feast outputs/my-feast
export IOT_FEAST_SCRATCH=/srv/iot-fast/feast-scratch
docker compose -f infra/feast.compose.yaml build tools
docker compose -f infra/feast.compose.yaml up -d online
docker compose -f infra/feast.compose.yaml run --rm tools \
  -m feature_store.checks --output /workspace/outputs/my-feast/native
docker compose -f infra/feast.compose.yaml run --rm tools \
  -m feature_store.batch \
  --artifacts /workspace/data/feature-datasets/feature-medium-27-v2/artifacts \
  --feature-report /workspace/outputs/feature-medium-27-v2/feature-report.json \
  --state /workspace/data/feast/my-batch \
  --reports /workspace/outputs/my-feast/batch \
  --protocol /workspace/ml/protocols/medium-60d-v1.json
docker compose -f infra/feast.compose.yaml run --rm tools \
  -m feature_store.load \
  --state /workspace/data/feast/my-batch \
  --batch-report /workspace/outputs/my-feast/batch/report.json \
  --artifacts /workspace/data/feature-datasets/feature-medium-27-v2/artifacts \
  --feature-report /workspace/outputs/feature-medium-27-v2/feature-report.json \
  --protocol /workspace/ml/protocols/medium-60d-v1.json \
  --destination /workspace/data/feast/my-training \
  --reports /workspace/outputs/my-feast/load
docker compose -f infra/feast.compose.yaml run --rm tools \
  -m feature_store.verify \
  --dataset /workspace/data/feast/my-training \
  --load-report /workspace/outputs/my-feast/load/report.json \
  --artifacts /workspace/data/feature-datasets/feature-medium-27-v2/artifacts \
  --state /workspace/data/feast/my-batch \
  --protocol /workspace/ml/protocols/medium-60d-v1.json \
  --output /workspace/outputs/my-feast/exact.json
```

## Implementation and evidence

- [Source: infra/feast/Dockerfile](../infra/feast/Dockerfile)
- [Source: infra/feast/requirements.lock](../infra/feast/requirements.lock)
- [Recorded artifact: feast/image/pip-freeze.txt](evidence/feast/image/pip-freeze.txt)
- [Source: infra/feast.compose.yaml](../infra/feast.compose.yaml)
- [Recorded artifact: feast/online-runtime.json](evidence/feast/online-runtime.json)
- [Source: feature_store/repository.py](../feature_store/repository.py)
- [Source: ml/protocols/medium-60d-v1.json](../ml/protocols/medium-60d-v1.json)
- [Source: feature_store/checks.py](../feature_store/checks.py)
- [Recorded artifact: feast/native/report.json](evidence/feast/native/report.json)
- [Recorded artifact: feast/batch/report.json](evidence/feast/batch/report.json)
- [Recorded artifact: feast/batch/compaction.json](evidence/feast/batch/compaction.json)
- [Recorded artifact: feast/batch/registration.json](evidence/feast/batch/registration.json)
- [Recorded artifact: feast/full-load/result/report.json](evidence/feast/full-load/result/report.json)
- [Recorded artifact: feast/audit/exact.json](evidence/feast/audit/exact.json)
- [Recorded artifact: feast/audit/verification.json](evidence/feast/audit/verification.json)
- [Source: feature_store/load.py](../feature_store/load.py)
- [pypi.org / 0.67.0](https://pypi.org/project/feast/0.67.0/)
- [docs.feast.dev / duckdb](https://docs.feast.dev/reference/offline-stores/duckdb)
- [docs.feast.dev / redis](https://docs.feast.dev/reference/online-stores/redis)
- [docs.feast.dev / point-in-time-joins](https://docs.feast.dev/getting-started/concepts/point-in-time-joins)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/a395c4ea47b8f8e235556ab93de3d699ed9df18b/docs/feast-feature-store.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
