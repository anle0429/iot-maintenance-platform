# A/B assignment and replay semantics

The A/B router selects an experimental model deterministically and records its decision in a persistent ledger. Retrying the same request must reuse the assignment; a conflicting payload must be rejected rather than creating another trial.

The early local checks establish assignment, replay and boundary behavior on controlled fixtures. The later native experiment and CI deployment use their own immutable model/source bindings, retained storage and HTTP evidence. They are documented in `ci-ab-deployment.md` and `ab-monitoring.md`.

Keep request counts, assignment counts and distinct feature snapshots separate when reading results. Monitoring without labels describes serving behavior and input distributions, not comparative prediction quality.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
python -m pytest tests/api/test_ab_api.py -q
$env:IOT_AB_PROOF_DIR = Join-Path (Get-Location).Path 'outputs/ab-demo'
try { python -m pytest tests/integration/test_ab_routing_http.py -q }
finally { Remove-Item Env:IOT_AB_PROOF_DIR }
```

## Implementation and evidence

- [Recorded artifact: ab-routing-pc/verification.json](evidence/ab-routing-pc/verification.json)
- [Source: services/ab_api/app.py](../services/ab_api/app.py)
- [Source: services/feature_api/runtime.py](../services/feature_api/runtime.py)
- [Source: services/ab_api/runtime.py](../services/ab_api/runtime.py)
- [Source: services/drift_api/detector.py](../services/drift_api/detector.py)
- [Source: services/ab_api/metrics.py](../services/ab_api/metrics.py)
- [Source: infra/ab-api/grafana-dashboard.json](../infra/ab-api/grafana-dashboard.json)
- [Recorded artifact: ab-routing-pc/source.manifest.json](evidence/ab-routing-pc/source.manifest.json)
- [Source: tests/integration/test_ab_routing_http.py](../tests/integration/test_ab_routing_http.py)
- [Recorded artifact: ab-routing-pc/http-readback.json](evidence/ab-routing-pc/http-readback.json)
- [Recorded artifact: ab-routing-pc/ab-metrics.txt](evidence/ab-routing-pc/ab-metrics.txt)
- [Recorded artifact: ab-routing-pc/http-verification.json](evidence/ab-routing-pc/http-verification.json)
- [Recorded artifact: ab-routing-pc/helm-rendered.yaml](evidence/ab-routing-pc/helm-rendered.yaml)
- [Recorded artifact: ab-routing-pc/manifest.json](evidence/ab-routing-pc/manifest.json)
- [Source: infra/ci/requirements-serving-tests.lock](../infra/ci/requirements-serving-tests.lock)
- [Source: infra/ab-api/Dockerfile](../infra/ab-api/Dockerfile)
- [Source: infra/ab-api/helm-values.yaml](../infra/ab-api/helm-values.yaml)
- [prometheus.github.io / custom](https://prometheus.github.io/client_python/collector/custom/)
- [grafana.com / view-dashboard-json-model](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/view-dashboard-json-model/)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/ab-testing.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
