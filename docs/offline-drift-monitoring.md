# Scheduled offline drift monitoring

Airflow periodically reads up to 1,024 snapshots through native Feast, compares eight inputs with a fixed model 3 training reference using PSI, and publishes gauges to Pushgateway. It does not read labels or infer model 6 accuracy.

Two recorded scheduled runs five minutes apart succeeded. The verifier recomputes histograms, PSI, policy flags and 23 gauges, then compares 20 panel values from six Grafana panels through both query engines. The two overlapping windows are not 2,048 independent observations.

Inputs, protocol and reference hashes are checked before and after retrieval. Corrupted input does not publish a new success metric. A reachable Pushgateway or Prometheus scrape is not proof of a recent successful comparison; the dashboard shows result age.

The workload maps actual schedule time into a finite simulated source. It fails when that source ends instead of replaying old windows indefinitely. Retraining is a separately guarded controller step.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
python -m unittest tests.test_drift_monitoring tests.test_offline_drift_oracle -v
```

```powershell
python -S scripts/verify-offline-drift.py
python -m unittest tests.test_drift_monitoring tests.test_offline_drift_oracle -v
```

```bash
python -m feature_store.drift_monitor \
  --state /workspace/data/feast/batch-medium-27 \
  --batch-report /inputs/batch-report.json \
  --reference /inputs/reference.json --config /inputs/clock.json \
  --output /output/new-attempt --interval-end 2026-10-28T03:20:00Z
```

## Implementation and evidence

- [Source: airflow/dags/iot_offline_drift.py](../airflow/dags/iot_offline_drift.py)
- [Source: feature_store/drift_airflow.py](../feature_store/drift_airflow.py)
- [Source: feature_store/drift_monitor.py](../feature_store/drift_monitor.py)
- [github.com / v1.11.4](https://github.com/prometheus/pushgateway/releases/tag/v1.11.4)
- [Source: infra/monitoring/pushgateway-linux.json](../infra/monitoring/pushgateway-linux.json)
- [prometheus.io / pushing](https://prometheus.io/docs/practices/pushing/)
- [github.com / README.md](https://github.com/prometheus/pushgateway/blob/master/README.md)
- [Recorded artifact: offline-drift-linux/manifest.json](evidence/offline-drift-linux/manifest.json)
- [Source: scripts/verify-offline-drift.py](../scripts/verify-offline-drift.py)
- [Recorded artifact: offline-drift-linux/negative/verification.json](evidence/offline-drift-linux/negative/verification.json)
- [airflow.apache.org / dag-run.html](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dag-run.html)
- [grafana.com / configure-standard-options](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/configure-standard-options/)
- [Recorded artifact: offline-drift-linux/date-before.jpg](evidence/offline-drift-linux/date-before.jpg)
- [Recorded artifact: offline-drift-linux/runtime/clock.json](evidence/offline-drift-linux/runtime/clock.json)
- [Recorded artifact: offline-drift-linux/runtime/config.json](evidence/offline-drift-linux/runtime/config.json)

## Recorded interface views

These are original captures from the runs described above. Their timestamps, model identities and visible states belong to those runs.

![Recorded dashboard overview](evidence/offline-drift-linux/dashboard-overview.jpg)

![Recorded dashboard clocks](evidence/offline-drift-linux/dashboard-clocks.jpg)


## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/offline-drift-monitoring.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
