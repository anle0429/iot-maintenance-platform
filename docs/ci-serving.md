# Serving CI: tests, images, A/B and rollback

The recorded Jenkins serving build 8 completed successfully on the VM using source `99d6dcc`. It exported the lab's production-tagged model 6, tested and built the serving images, ran native probes, deployed the experiment and switched the feature API after its gates passed.

The gates include 104 contracts and 208 API tests; recorded statement coverage is 100% over 425 statements and branch coverage is 125/128 (97.66%). A complete, source-checked parent mutation run killed 1,309/1,582 mutants (82.74%). Reuse is bound to unchanged relevant source, not a new mutation run for every release.

The A/B trial recorded 780 assignments and 40 replay requests without increasing assignment counts. The full cohort produced 195 valid predictions and five stopped-machine 409 responses. Controlled rollback exercised 6→1→6. Locust runs at 1/10 users recorded 598/5,780 requests, zero failures and P95 7/28 ms.

These are bounded same-VM lab measurements, not maximum capacity or proof that model 6 is better. Original failed attempts, source bindings, replica restoration and UI captures remain in the evidence.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
python -S scripts/verify-native-serving.py
```

```bash
python -m pytest tests/test_serving_ci.py tests/test_ci_training.py tests/test_serving_release.py -q
python -S scripts/ci-serving.py --help
python scripts/package-serving-release.py --commit d811a7853b431ba5bb3d248d4526aad5f3ff635e --output outputs/serving-review.tar
helm lint infra/charts/iot-api -f docs/evidence/serving-ci-preparation/current/fixture-feature-values.json
helm template iot-feature-api infra/charts/iot-api -n iot-features --kube-version 1.35.9 -f docs/evidence/serving-ci-preparation/current/fixture-feature-values.json
```

## Implementation and evidence

- [Recorded artifact: ci-serving-linux/ci/tests/contracts.txt](evidence/ci-serving-linux/ci/tests/contracts.txt)
- [Recorded artifact: ci-serving-linux/ci/tests/api-tests.txt](evidence/ci-serving-linux/ci/tests/api-tests.txt)
- [Recorded artifact: ci-serving-linux/ci/tests/coverage-html/index.html](evidence/ci-serving-linux/ci/tests/coverage-html/index.html)
- [Recorded artifact: ci-serving-linux/ci/tests/verification.json](evidence/ci-serving-linux/ci/tests/verification.json)
- [Recorded artifact: ci-serving-linux/ci/tests/mutation-provenance.json](evidence/ci-serving-linux/ci/tests/mutation-provenance.json)
- [Recorded artifact: ci-serving-linux/mutation-parent5/tests/mutmut-all-results.txt](evidence/ci-serving-linux/mutation-parent5/tests/mutmut-all-results.txt)
- [Recorded artifact: ci-serving-linux/ci/ab-verification.json](evidence/ci-serving-linux/ci/ab-verification.json)
- [Recorded artifact: ci-serving-linux/independent-hdd-ledger.json](evidence/ci-serving-linux/independent-hdd-ledger.json)
- [Recorded artifact: ci-serving-linux/ci/http-all-machines.json](evidence/ci-serving-linux/ci/http-all-machines.json)
- [Recorded artifact: ci-serving-linux/rollback/verification.json](evidence/ci-serving-linux/rollback/verification.json)
- [Recorded artifact: ci-serving-linux/monitoring/panel-queries.json](evidence/ci-serving-linux/monitoring/panel-queries.json)
- [Recorded artifact: ci-serving-linux/manifest.json](evidence/ci-serving-linux/manifest.json)
- [Recorded artifact: ci-serving-linux/failed-fallback7/rollback-to-baseline.txt](evidence/ci-serving-linux/failed-fallback7/rollback-to-baseline.txt)
- [Recorded artifact: ci-serving-linux/failed-fallback7/baseline-redis-diagnostic.json](evidence/ci-serving-linux/failed-fallback7/baseline-redis-diagnostic.json)
- [Recorded artifact: ci-serving-linux/failed-ab6/ab.txt](evidence/ci-serving-linux/failed-ab6/ab.txt)
- [Recorded artifact: ci-serving-linux/load/users-1.html](evidence/ci-serving-linux/load/users-1.html)
- [Recorded artifact: ci-serving-linux/load/users-10.html](evidence/ci-serving-linux/load/users-10.html)
- [github.com / stats.py](https://github.com/locustio/locust/blob/2.47.0/locust/stats.py)
- [github.com / redis.py](https://github.com/feast-dev/feast/blob/v0.67.0/sdk/python/feast/infra/online_stores/redis.py)
- [kserve.github.io / hpa-autoscaler](https://kserve.github.io/website/docs/model-serving/predictive-inference/autoscaling/hpa-autoscaler)
- [www.jenkins.io / syntax](https://www.jenkins.io/doc/book/pipeline/syntax/#post)
- [Source: ml/ab_native.py](../ml/ab_native.py)
- [Source: ml/serving_gate_reuse.py](../ml/serving_gate_reuse.py)
- [Source: infra/ci/Serving.Jenkinsfile](../infra/ci/Serving.Jenkinsfile)
- [Source: scripts/ci-serving.py](../scripts/ci-serving.py)
- [Source: scripts/test-serving-ci.py](../scripts/test-serving-ci.py)
- [Source: ml/serving_ci.py](../ml/serving_ci.py)
- [Recorded artifact: serving-ci-preparation/current/verification.json](evidence/serving-ci-preparation/current/verification.json)
- [Recorded artifact: serving-ci-preparation/current/helm-rendered.yaml](evidence/serving-ci-preparation/current/helm-rendered.yaml)
- [Recorded artifact: serving-ci-preparation/current/source-v4.manifest.json](evidence/serving-ci-preparation/current/source-v4.manifest.json)
- [Recorded artifact: serving-ci-preparation/current/training-source-v4.manifest.json](evidence/serving-ci-preparation/current/training-source-v4.manifest.json)
- [Recorded artifact: serving-ci-preparation/verification.json](evidence/serving-ci-preparation/verification.json)
- [Recorded artifact: serving-ci-preparation/old-source-rejection.json](evidence/serving-ci-preparation/old-source-rejection.json)
- [Recorded artifact: serving-ci-preparation/current/manifest.json](evidence/serving-ci-preparation/current/manifest.json)
- [Source: infra/ci/requirements-serving-tests.lock](../infra/ci/requirements-serving-tests.lock)
- [kserve.github.io / custom-predictor](https://kserve.github.io/website/docs/model-serving/predictive-inference/frameworks/custom-predictor)
- [docs.helm.sh / helm_upgrade](https://docs.helm.sh/docs/v3/helm/helm_upgrade/)
- [mutmut.readthedocs.io / latest](https://mutmut.readthedocs.io/en/latest/)

## Recorded interface views

These are original captures from the runs described above. Their timestamps, model identities and visible states belong to those runs.

![Recorded jenkins8 success](evidence/ci-serving-linux/screenshots/jenkins8-success.jpg)

![Recorded api tests8 linux](evidence/ci-serving-linux/screenshots/api-tests8-linux.jpg)

![Recorded api coverage8 linux](evidence/ci-serving-linux/screenshots/api-coverage8-linux.jpg)

![Recorded api gate8 linux](evidence/ci-serving-linux/screenshots/api-gate8-linux.jpg)

![Recorded mutation provenance8 linux](evidence/ci-serving-linux/screenshots/mutation-provenance8-linux.jpg)

![Recorded locust8 users 1](evidence/ci-serving-linux/screenshots/locust8-users-1.jpg)

![Recorded locust8 users 10](evidence/ci-serving-linux/screenshots/locust8-users-10.jpg)


## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/ci-serving.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
