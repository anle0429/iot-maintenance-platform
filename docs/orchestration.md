# Airflow DP1–DP3, validation and DataHub lineage

The native pilot runs DP1 Bronze→DP2 Silver/Gold→DP3 features/labels through Airflow 3.1.7. Each stage ingests, validates and publishes metadata before triggering the next. Spark 3.5.9 jobs run through a one-slot pool with explicit resource limits.

Two clean batches passed 64 rules across 14 outputtables. A separate batch with a null machine key was rejected, blocking downstream DP2/DP3 publication; recovery preserved the failure history. The gate included 91 unit tests and 11 known-answer Spark fixtures.

DataHub receives schemas, producing jobs, table lineage and external assertions through its SDK. GMS/UI readbacks establish that metadata was actually indexed. Images and source/config identities are recorded, and database/broker/search ports remain private.

This pilot uses Parquet Gold and a bounded source. It does not establish a 100 GB full-feature Airflow run or a SQL warehouse deployment. The full-scale Bronze/Silver runner, later feature stores and DataHub restoration are separate workloads.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```bash
docker build -t iot-coursework-spark:3.5.9 -f infra/spark/Dockerfile .
sudo python3 scripts/start-orchestration.py
sudo python3 scripts/verify-orchestration.py prepare
sudo python3 scripts/verify-orchestration.py health
sudo python3 scripts/verify-orchestration.py trigger --batch my-good-run
sudo python3 scripts/verify-orchestration.py snapshot --batch my-good-run
sudo python3 scripts/verify-orchestration.py metadata --batch my-good-run
```

```bash
sudo python3 scripts/verify-orchestration.py trigger --batch fixture-null-machine --fault null_machine
sudo python3 scripts/verify-orchestration.py snapshot --batch fixture-null-machine
```

```powershell
gcloud compute ssh iot-cw-vm --project=$projectId --zone=us-central1-a --tunnel-through-iap --quiet `
  --ssh-flag=-N --ssh-flag=-L --ssh-flag=127.0.0.1:18480:127.0.0.1:18480 `
  --ssh-flag=-L --ssh-flag=127.0.0.1:19002:127.0.0.1:19002
```

## Implementation and evidence

- [Recorded artifact: orchestration/verification.json](evidence/orchestration/verification.json)
- [Recorded artifact: orchestration/runtime.json](evidence/orchestration/runtime.json)
- [Recorded artifact: orchestration/screenshots/airflow-three-dags-recovered.jpg](evidence/orchestration/screenshots/airflow-three-dags-recovered.jpg)
- [Recorded artifact: orchestration/orchestration-recovery-26-airflow.json](evidence/orchestration/orchestration-recovery-26-airflow.json)
- [Recorded artifact: orchestration/orchestration-recovery-26-datahub.json](evidence/orchestration/orchestration-recovery-26-datahub.json)
- [Recorded artifact: orchestration/fixture-null-machine-26-airflow.json](evidence/orchestration/fixture-null-machine-26-airflow.json)
- [Recorded artifact: orchestration/screenshots/airflow-dp1-success.jpg](evidence/orchestration/screenshots/airflow-dp1-success.jpg)
- [Recorded artifact: orchestration/screenshots/airflow-dp2-success.jpg](evidence/orchestration/screenshots/airflow-dp2-success.jpg)
- [Recorded artifact: orchestration/screenshots/airflow-dp3-success.jpg](evidence/orchestration/screenshots/airflow-dp3-success.jpg)
- [Recorded artifact: orchestration/screenshots/airflow-validation-blocked.jpg](evidence/orchestration/screenshots/airflow-validation-blocked.jpg)
- [Recorded artifact: orchestration/screenshots/datahub-feature-schema.jpg](evidence/orchestration/screenshots/datahub-feature-schema.jpg)
- [Recorded artifact: orchestration/screenshots/datahub-feature-lineage.jpg](evidence/orchestration/screenshots/datahub-feature-lineage.jpg)
- [Recorded artifact: orchestration/screenshots/datahub-feature-quality.jpg](evidence/orchestration/screenshots/datahub-feature-quality.jpg)
- [Recorded artifact: orchestration/screenshots/datahub-validation-pass-fail-pass.jpg](evidence/orchestration/screenshots/datahub-validation-pass-fail-pass.jpg)
- [Recorded artifact: orchestration/screenshots/datahub-catalog.jpg](evidence/orchestration/screenshots/datahub-catalog.jpg)
- [Recorded artifact: orchestration/git-source-mapping.json](evidence/orchestration/git-source-mapping.json)
- [airflow.apache.org / upgrading_to_airflow3.html](https://airflow.apache.org/docs/apache-airflow/3.1.7/installation/upgrading_to_airflow3.html)
- [github.com / docker-compose.quickstart-profile.yml](https://github.com/datahub-project/datahub/blob/v1.6.0.3/docker/quickstart/docker-compose.quickstart-profile.yml)
- [github.com / v1.6.0.3](https://github.com/datahub-project/datahub/releases/tag/v1.6.0.3)
- [Source: infra/datahub/UPSTREAM_LICENSE](../infra/datahub/UPSTREAM_LICENSE)
- [github.com / assertions.md](https://github.com/datahub-project/datahub/blob/v1.6.0/docs/api/tutorials/assertions.md)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/a395c4ea47b8f8e235556ab93de3d699ed9df18b/docs/orchestration.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
