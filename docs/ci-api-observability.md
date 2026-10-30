# CI for API metrics and tracing

Jenkins telemetry build 2 succeeded with source `372`. Its test/build/auto-deploy path exercised the observability wrapper around the feature, predictor and drift APIs while retaining model 6.

The native gate recorded 299 main tests and 21 subtests, 100% coverage of the wrapper's 74 statements/16 branches, and 221/250 killed mutants (88.4%). Three images were built and probed; native HTTP checks covered 200 machines. Recorded image identities and code hashes connect the tests with the deployed workload.

The publication contains 199 payloads and original screenshots across the observability/CI evidence packages. This release adds telemetry; it does not train or promote another model, create a new A/B comparison, or establish production HA. The earlier serving mutation run and the wrapper mutation run have different source scopes.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
python -S scripts/verify-ci-api-observability.py
python -m pytest tests/test_observability_ci.py tests/test_serving_ci.py tests/test_ab_ci.py tests/test_serving_gate_reuse.py -q
python -m pytest tests/api/test_observed.py tests/test_api_observability_gate.py -q
python scripts/package-serving-release.py --commit HEAD --output outputs/telemetry-release.tar
```

## Implementation and evidence

- [Source: infra/ci/Observability.Jenkinsfile](../infra/ci/Observability.Jenkinsfile)
- [Source: scripts/ci-observability.py](../scripts/ci-observability.py)
- [Recorded artifact: ci-api-observability/jenkins/build-2.json](evidence/ci-api-observability/jenkins/build-2.json)
- [Recorded artifact: ci-api-observability/jenkins/stages-2.json](evidence/ci-api-observability/jenkins/stages-2.json)
- [Recorded artifact: ci-api-observability/screenshots/jenkins-telemetry2-success.jpg](evidence/ci-api-observability/screenshots/jenkins-telemetry2-success.jpg)
- [Recorded artifact: ci-api-observability/screenshots/jenkins-telemetry2-stages.jpg](evidence/ci-api-observability/screenshots/jenkins-telemetry2-stages.jpg)
- [Recorded artifact: ci-api-observability/current/telemetry-tests/gate/verification.json](evidence/ci-api-observability/current/telemetry-tests/gate/verification.json)
- [Recorded artifact: ci-api-observability/current/http-all-machines.json](evidence/ci-api-observability/current/http-all-machines.json)
- [Recorded artifact: ci-api-observability/history/build-1/tests-overlay-build.txt](evidence/ci-api-observability/history/build-1/tests-overlay-build.txt)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/a395c4ea47b8f8e235556ab93de3d699ed9df18b/docs/ci-api-observability.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
