# Version consistency across feature and prediction APIs

Both APIs enforce the same selected model pin: model name/version, run, protocol, portable-artifact hash, training source, data version, threshold and production flag. The predictor derives its identity from verified lineage; the feature API rejects a response whose identity differs.

Eligibility is independent of the numeric model. Valid feature timestamps cannot compensate for a wrong model, and a correct model cannot make unavailable or stopped-machine features eligible. The response retains both model identity and feature provenance.

Boundary, mutation and native image/deployment checks have distinct source scopes. The demonstrated serving pin is version 6; retraining version 7 remains a candidate until an explicit delivery gate promotes it.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```bash
python -m pytest tests/api -q -ra --cov=services.predictor --cov=services.feature_api --cov=services.drift_api --cov-branch --cov-report=term-missing --cov-report=html:outputs/versioned-api-html
python scripts/write-model-pin.py --bundle models/pump-failure-v1 --output outputs/legacy-pin.json
```

```bash
python scripts/export-production-model.py --tracking-uri http://tracking:5000 --version "$MODEL_VERSION" --output models/release
python scripts/write-model-pin.py --bundle models/release --output models/release/model-pin.json
docker build -f infra/predictor/Dockerfile --build-arg MODEL_BUNDLE=models/release -t iot-predictor:release .
docker build -f infra/feature-api/Dockerfile --build-arg MODEL_PIN=models/release/model-pin.json -t iot-feature-api:release .
```

## Implementation and evidence

- [Source: services/predictor/binding.py](../services/predictor/binding.py)
- [Source: services/predictor/runtime.py](../services/predictor/runtime.py)
- [Source: scripts/write-model-pin.py](../scripts/write-model-pin.py)
- [Source: services/feature_api/app.py](../services/feature_api/app.py)
- [Source: models/pump-failure-v1/model-pin.json](../models/pump-failure-v1/model-pin.json)
- [Recorded artifact: version-pin-preparation/verification.json](evidence/version-pin-preparation/verification.json)
- [Recorded artifact: version-pin-preparation/manifest.json](evidence/version-pin-preparation/manifest.json)
- [Recorded artifact: version-pin-preparation/api-tests.txt](evidence/version-pin-preparation/api-tests.txt)
- [Recorded artifact: version-pin-preparation/coverage.json](evidence/version-pin-preparation/coverage.json)
- [Recorded artifact: version-pin-preparation/loopback-http.json](evidence/version-pin-preparation/loopback-http.json)
- [Recorded artifact: version-pin-preparation/public-cli.json](evidence/version-pin-preparation/public-cli.json)
- [Recorded artifact: version-pin-preparation/adjacent-tests.txt](evidence/version-pin-preparation/adjacent-tests.txt)
- [Source: pyproject.toml](../pyproject.toml)
- [Source: infra/predictor/requirements-test.txt](../infra/predictor/requirements-test.txt)
- [mutmut.readthedocs.io / latest](https://mutmut.readthedocs.io/en/latest/)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/versioned-serving.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
