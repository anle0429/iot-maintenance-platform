# API metrics and distributed traces

The feature, predictor and drift APIs use the observability wrapper to record HTTP metrics and OTLP spans. Native verification compares six panels and 28 values from each Prometheus/Grafana engine, plus five traces with 15 spans and their parent relationships.

The recorded client workload has 747 requests and 932 SDK-side requests; the difference is explained by the actual internal call ledger. The wrapper's native gate passed 296 main tests and 14 subtests, covered 74 statements/16 branches completely, and killed 221/250 mutants (88.4%).

The rollout retained model 6 and its feature protocol. Trace evidence covers the observed HTTP path; it does not assert spans inside every Redis/Feast operation. Jaeger uses bounded in-memory storage in this lab, so a restart can remove historical traces. Current English dashboard labels do not retroactively translate the original screenshots.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
python -S scripts/verify-api-observability.py
```

```powershell
python -m pytest tests/api/test_observed.py tests/test_api_observability_gate.py -q
```

## Implementation and evidence

- [Recorded artifact: api-observability-linux/gate/current/api-tests.txt](evidence/api-observability-linux/gate/current/api-tests.txt)
- [Recorded artifact: api-observability-linux/gate/current/junit.xml](evidence/api-observability-linux/gate/current/junit.xml)
- [Recorded artifact: api-observability-linux/gate/current/coverage-html/index.html](evidence/api-observability-linux/gate/current/coverage-html/index.html)
- [Recorded artifact: api-observability-linux/screenshots/native-coverage.jpg](evidence/api-observability-linux/screenshots/native-coverage.jpg)
- [Recorded artifact: api-observability-linux/gate/current/observed.py.meta](evidence/api-observability-linux/gate/current/observed.py.meta)
- [Recorded artifact: api-observability-linux/gate/current/mutmut-all-results.txt](evidence/api-observability-linux/gate/current/mutmut-all-results.txt)
- [Recorded artifact: api-observability-linux/screenshots/native-gate.jpg](evidence/api-observability-linux/screenshots/native-gate.jpg)
- [Recorded artifact: api-observability-linux/native/api-rollout/verification.json](evidence/api-observability-linux/native/api-rollout/verification.json)
- [Recorded artifact: api-observability-linux/native/api-rollout/http-all-machines.json](evidence/api-observability-linux/native/api-rollout/http-all-machines.json)
- [Recorded artifact: api-observability-linux/native/workload/run/requests.jsonl](evidence/api-observability-linux/native/workload/run/requests.jsonl)
- [Recorded artifact: api-observability-linux/native/workload/run/actual-deltas.json](evidence/api-observability-linux/native/workload/run/actual-deltas.json)
- [Recorded artifact: api-observability-linux/native/workload/drift-ready-before.json](evidence/api-observability-linux/native/workload/drift-ready-before.json)
- [Recorded artifact: api-observability-linux/native/workload/drift-ready-after.json](evidence/api-observability-linux/native/workload/drift-ready-after.json)
- [Recorded artifact: api-observability-linux/native/metrics-readback/panel-readbacks.json](evidence/api-observability-linux/native/metrics-readback/panel-readbacks.json)
- [Recorded artifact: api-observability-linux/screenshots/api-dashboard-overview.jpg](evidence/api-observability-linux/screenshots/api-dashboard-overview.jpg)
- [Recorded artifact: api-observability-linux/screenshots/api-dashboard-details.jpg](evidence/api-observability-linux/screenshots/api-dashboard-details.jpg)
- [Recorded artifact: api-observability-linux/native/trace-readback/searched-traces.json](evidence/api-observability-linux/native/trace-readback/searched-traces.json)
- [Recorded artifact: api-observability-linux/screenshots/jaeger-three-spans.jpg](evidence/api-observability-linux/screenshots/jaeger-three-spans.jpg)
- [Recorded artifact: api-observability-linux/screenshots/jaeger-span-details.jpg](evidence/api-observability-linux/screenshots/jaeger-span-details.jpg)
- [Source: services/observed.py](../services/observed.py)
- [Recorded artifact: api-observability-linux/native/source/api.json](evidence/api-observability-linux/native/source/api.json)
- [Recorded artifact: api-observability-linux/native/source/config.json](evidence/api-observability-linux/native/source/config.json)
- [Recorded artifact: api-observability-linux/native/image-build/verification.json](evidence/api-observability-linux/native/image-build/verification.json)
- [Source: infra/observability/requirements.lock](../infra/observability/requirements.lock)
- [Source: infra/observability/jaeger-assets.json](../infra/observability/jaeger-assets.json)
- [Source: infra/observability/jaeger.yaml](../infra/observability/jaeger.yaml)
- [Recorded artifact: api-observability-linux/gate/history/score-equal-eighty/results/tests/mutmut-cicd-stats.json](evidence/api-observability-linux/gate/history/score-equal-eighty/results/tests/mutmut-cicd-stats.json)
- [Recorded artifact: api-observability-linux/native/history/missing-packaging/drift-build.txt](evidence/api-observability-linux/native/history/missing-packaging/drift-build.txt)
- [Recorded artifact: api-observability-linux/runtime-restore/readback.json](evidence/api-observability-linux/runtime-restore/readback.json)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/api-observability.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
