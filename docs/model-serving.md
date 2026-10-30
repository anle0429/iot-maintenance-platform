# Portable model serving and lineage

The predictor packages an exported logistic model with its lineage and validates both before becoming ready. It checks the protocol, ordered eight-feature schema, scaler/coefficient dimensions, finite numeric metadata and selected threshold.

Portable inference reproduces the Spark model's numeric calculation. A response carries score/decision and model identity; the feature API checks that identity against its own pin. A different registry version must not silently satisfy an old release contract.

Early local/image checks and later native KServe/CI releases have separate source bindings. Their historical results remain attached to the models actually used. A score between zero and one is not automatically a calibrated failure probability.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```json
{"instances":[{"temperature_mean_10m":62.0,"vibration_max_10m":5.0,"current_mean_10m":16.0,"current_std_10m":1.6,"rpm_mean_10m":2800.0,"reading_count_10m":9,"machine_age_days":900.0,"prior_failures_24h":1}]}
```

```bash
docker compose -f infra/predictor.compose.yaml build predictor
docker compose -f infra/predictor.compose.yaml up -d predictor
docker compose -f infra/predictor.compose.yaml ps
docker compose -f infra/predictor.compose.yaml exec predictor python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health/ready').read().decode())"
```

```bash
python -m venv outputs/predictor-test-env
# Linux: outputs/predictor-test-env/bin/python; Windows: .../Scripts/python.exe
outputs/predictor-test-env/bin/python -m pip install -r infra/predictor/requirements.lock -r infra/predictor/requirements-test.txt
outputs/predictor-test-env/bin/python -m pytest tests/api/test_predictor.py --cov=services.predictor --cov-branch --cov-report=term-missing
```

```bash
outputs/predictor-test-env/bin/mutmut run --max-children 2
outputs/predictor-test-env/bin/mutmut results
```

## Implementation and evidence

- [Source: services/predictor/runtime.py](../services/predictor/runtime.py)
- [Recorded artifact: predictor/image/verification.json](evidence/predictor/image/verification.json)
- [Recorded artifact: predictor/image/build-console.txt](evidence/predictor/image/build-console.txt)
- [Source: infra/predictor/requirements.lock](../infra/predictor/requirements.lock)
- [Recorded artifact: predictor/image/requirements-installed.lock](evidence/predictor/image/requirements-installed.lock)
- [Source: tests/fixtures/predictor-native-spark.json](../tests/fixtures/predictor-native-spark.json)
- [Recorded artifact: predictor/image/http-predictions.json](evidence/predictor/image/http-predictions.json)
- [Source: models/pump-failure-v1/lineage.json](../models/pump-failure-v1/lineage.json)
- [Source: services/predictor/app.py](../services/predictor/app.py)
- [Source: services/predictor/contracts.py](../services/predictor/contracts.py)
- [Source: tests/api/test_predictor.py](../tests/api/test_predictor.py)
- [Recorded artifact: predictor/pc-v2/junit.xml](evidence/predictor/pc-v2/junit.xml)
- [Recorded artifact: predictor/pc-v2/console.txt](evidence/predictor/pc-v2/console.txt)
- [Recorded artifact: predictor/pc-v2/binding.json](evidence/predictor/pc-v2/binding.json)
- [Recorded artifact: predictor/pc/binding.json](evidence/predictor/pc/binding.json)
- [Recorded artifact: predictor/pc/coverage.json](evidence/predictor/pc/coverage.json)
- [Recorded artifact: predictor/pc/screenshots/capture.json](evidence/predictor/pc/screenshots/capture.json)
- [Recorded artifact: predictor/mutation-initial/completion.json](evidence/predictor/mutation-initial/completion.json)
- [Recorded artifact: predictor/mutation-initial/mutmut-console.txt](evidence/predictor/mutation-initial/mutmut-console.txt)
- [Recorded artifact: predictor/mutation-initial/surviving-diffs.json](evidence/predictor/mutation-initial/surviving-diffs.json)
- [Recorded artifact: predictor/mutation/verification.json](evidence/predictor/mutation/verification.json)
- [Recorded artifact: predictor/mutation/mutmut-console.txt](evidence/predictor/mutation/mutmut-console.txt)
- [Recorded artifact: predictor/mutation/surviving-diffs.json](evidence/predictor/mutation/surviving-diffs.json)
- [Recorded artifact: predictor/mutation/baseline-console.txt](evidence/predictor/mutation/baseline-console.txt)
- [Recorded artifact: predictor/mutation/preflight.json](evidence/predictor/mutation/preflight.json)
- [Source: infra/predictor/Dockerfile](../infra/predictor/Dockerfile)
- [Source: infra/predictor.compose.yaml](../infra/predictor.compose.yaml)
- [Source: pyproject.toml](../pyproject.toml)
- [Recorded artifact: predictor/capture/evidence-map.json](evidence/predictor/capture/evidence-map.json)
- [kserve.github.io / custom-predictor](https://kserve.github.io/website/docs/model-serving/predictive-inference/frameworks/custom-predictor)
- [fastapi.tiangolo.com / testing](https://fastapi.tiangolo.com/tutorial/testing/)
- [mutmut.readthedocs.io / latest](https://mutmut.readthedocs.io/en/latest/)

## Recorded interface views

These are original captures from the runs described above. Their timestamps, model identities and visible states belong to those runs.

![Recorded coverage](evidence/predictor/pc/screenshots/coverage.jpg)


## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/model-serving.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
