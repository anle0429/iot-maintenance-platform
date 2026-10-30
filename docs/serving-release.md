# Serving release bundle and model pin

A serving release binds the selected registry model, portable artifact, protocol, data version, source and threshold into one immutable bundle. Feature and predictor services validate that bundle before advertising readiness or returning inference.

The release gates reject incomplete lineage, inconsistent flags, altered artifact bytes and mismatched feature/model protocols. Local API fixtures establish request behavior; image construction and native rollout require separate evidence.

The original release document predates the completed native serving CI. Later CI 8 evidence records image builds, import/probes, A/B, cohort inference and rollback for model 6. Do not treat an early fixture-only package as that native rollout, or rewrite the old package's source pins.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```bash
python scripts/export-production-model.py \
  --tracking-uri http://tracking:5000 \
  --version "$MODEL_VERSION" \
  --output "outputs/serving-bundles/version-$MODEL_VERSION"
```

## Implementation and evidence

- [Source: ml/serving_release.py](../ml/serving_release.py)
- [Source: scripts/export-production-model.py](../scripts/export-production-model.py)
- [Source: tests/test_serving_release.py](../tests/test_serving_release.py)
- [Recorded artifact: serving-release-preparation/native-registry/verification.json](evidence/serving-release-preparation/native-registry/verification.json)
- [Recorded artifact: serving-release-preparation/native-registry/versions.json](evidence/serving-release-preparation/native-registry/versions.json)
- [Recorded artifact: serving-release-preparation/pc-negative-check.json](evidence/serving-release-preparation/pc-negative-check.json)
- [Recorded artifact: serving-release-preparation/pc-unit-tests.txt](evidence/serving-release-preparation/pc-unit-tests.txt)
- [Recorded artifact: serving-release-preparation/pc-cloud/vm-state.json](evidence/serving-release-preparation/pc-cloud/vm-state.json)
- [Recorded artifact: serving-release-preparation/manifest.json](evidence/serving-release-preparation/manifest.json)
- [mlflow.org / workflow](https://mlflow.org/docs/latest/ml/model-registry/workflow/)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/a395c4ea47b8f8e235556ab93de3d699ed9df18b/docs/serving-release.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
