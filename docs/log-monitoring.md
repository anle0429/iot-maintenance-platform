# Native Loki/Alloy log collection

The recorded Linux pipeline collects actual logs from three host services and two APIs through Alloy into Loki and visualizes them in Grafana. It verifies 237 lines with exact message, timestamp and label matching, plus 164 windowed count samples computed independently.

An earlier attempt had 19 timestamp mismatches and remains in the history. The corrected run and collector restart checks keep original source bytes and readbacks instead of editing the failed receipt. Native captures establish the tested collection and query path.

This original static-source milestone is separate from later Kubernetes Pod discovery. Neither one proves exactly-once behavior for every crash, rotation or arbitrary production workload. The collector's state and permissions are operational dependencies.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```bash
python -S scripts/verify-linux-logs.py
python -S -m unittest tests.test_log_monitoring tests.test_log_monitoring_oracle -v
```

```bash
sudo python3 -S scripts/observe-log-sources.py --project-root "$PWD" --output outputs/log-sources-new.json
sudo python3 -S scripts/prepare-log-monitoring.py --project-root "$PWD" --sources outputs/log-sources-new.json --output "$PWD/data/runtime/logs-demo-new" --collection demo-new
```

```bash
data/runtime/logging-assets-28/loki-linux-amd64 -config.file=data/runtime/logs-demo-new/loki.yaml -verify-config=true
data/runtime/logging-assets-28/alloy-linux-amd64 validate data/runtime/logs-demo-new/config.alloy
```

## Implementation and evidence

- [Recorded artifact: log-monitoring-linux/manifest.json](evidence/log-monitoring-linux/manifest.json)
- [Recorded artifact: log-monitoring-linux/verification.json](evidence/log-monitoring-linux/verification.json)
- [Recorded artifact: log-monitoring-linux/source-map.json](evidence/log-monitoring-linux/source-map.json)
- [Recorded artifact: log-monitoring-linux/query-context.json](evidence/log-monitoring-linux/query-context.json)
- [grafana.com / loki.process](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.process/#stagetimestamp)
- [github.com / timestamp.go](https://github.com/grafana/alloy/blob/v1.21.2/internal/component/loki/process/stages/timestamp.go)
- [Recorded artifact: log-monitoring-linux/before-differences.json](evidence/log-monitoring-linux/before-differences.json)
- [grafana.com / structured-metadata](https://grafana.com/docs/loki/latest/get-started/labels/structured-metadata/)
- [Source: infra/monitoring/logging-assets.json](../infra/monitoring/logging-assets.json)
- [github.com / v3.7.9](https://github.com/grafana/loki/releases/tag/v3.7.9)
- [github.com / v1.21.2](https://github.com/grafana/alloy/releases/tag/v1.21.2)
- [Recorded artifact: log-monitoring-linux/keep-started.json](evidence/log-monitoring-linux/keep-started.json)

## Recorded interface views

These are original captures from the runs described above. Their timestamps, model identities and visible states belong to those runs.

![Recorded overview](evidence/log-monitoring-linux/overview.jpg)

![Recorded details](evidence/log-monitoring-linux/details.jpg)


## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/log-monitoring.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
