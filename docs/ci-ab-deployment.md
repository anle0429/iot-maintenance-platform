# Native A/B serving deployment

Serving CI 8 deploys an experiment between trained model versions 1 and 6 before switching the primary feature API. Native state checks include model pins, Pod identities, SQL ledger contents and retained PV/PVC state.

The recorded trial contains 780 assignments, split 376/404, and 40 replays that do not increment assignment counts. Snapshot counts 94/101 distinguish eligible feature observations from repeated requests. Dashboard queries and returned model identities are compared against the actual stored state.

The experiment temporarily constrains predictors to one replica and restores the recorded scaling bounds afterwards. The controlled rollback 6→1→6 is a delivery check. Without ground-truth outcomes, traffic split, latency and PSI do not prove superior model accuracy or authorize an automatic quality-based promotion.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
python -m pytest tests/test_ab_ci.py tests/test_serving_ci.py -q
$env:IOT_AB_CI_PROOF_DIR = Join-Path (Get-Location).Path 'outputs/ci-ab-demo'
try { python -m pytest tests/integration/test_ab_ci_http.py -q }
finally { Remove-Item Env:IOT_AB_CI_PROOF_DIR }
```

## Implementation and evidence

- [Source: infra/ci/Serving.Jenkinsfile](../infra/ci/Serving.Jenkinsfile)
- [Source: scripts/ci-serving.py](../scripts/ci-serving.py)
- [Source: ml/ab_native.py](../ml/ab_native.py)
- [Source: ml/ab_ci.py](../ml/ab_ci.py)
- [Recorded artifact: ci-serving-linux/screenshots/jenkins8-success.jpg](evidence/ci-serving-linux/screenshots/jenkins8-success.jpg)
- [Recorded artifact: ci-serving-linux/ci/ab-verification.json](evidence/ci-serving-linux/ci/ab-verification.json)
- [Recorded artifact: ci-ab-preparation/verification.json](evidence/ci-ab-preparation/verification.json)
- [Source: tests/integration/test_ab_ci_http.py](../tests/integration/test_ab_ci_http.py)
- [Recorded artifact: ci-ab-preparation/manifest.json](evidence/ci-ab-preparation/manifest.json)
- [kubernetes.io / configmap](https://kubernetes.io/docs/concepts/configuration/configmap/#immutable-configmaps)
- [kubernetes.io / storage-classes](https://kubernetes.io/docs/concepts/storage/storage-classes/#volume-binding-mode)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/ci-ab-deployment.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
