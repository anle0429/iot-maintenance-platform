# Feature-store integration

Feast SDK runs inside batch jobs and API processes. Offline Parquet retains history; online Redis holds the values used for serving. Event freshness, physical key expiry and the genuine prepared-data lease are separate checks.

| Boundary | Entry point | Contract |
|---|---|---|
| Repository definitions | [repository.py](repository.py), [common.py](common.py) | Entities, source/view identities and feature names |
| Batch publication | [batch.py](batch.py), [materialize.py](materialize.py) | Source-bound incremental registration/materialization |
| Serving state | [serving.py](serving.py) | Sealed prepared state and model/feature provenance |
| Finalized stream delivery | [staged_push.py](staged_push.py), [staged_checks.py](staged_checks.py) | Separate offline history and online latest-value writes with replay checks |
| Training examples | [training_samples.py](training_samples.py) | Eligible sample identities and target joins |
| Scheduled checks | [airflow_tasks.py](airflow_tasks.py), [drift_airflow.py](drift_airflow.py) | Airflow publication and drift orchestration |

The eight rolling model inputs differ from the four finalized-window monitoring aggregates. See [write paths and TTL](../docs/feature-jobs-and-ttl.md), [native materialization](../docs/feast-materialization.md) and [stream CI](../docs/ci-stream-push.md).
