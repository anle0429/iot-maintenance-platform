# Native MMD audit on a bound feature cohort

The native diagnostic compares 64 fixed training-reference rows with 64 operating-machine feature vectors from a retained genuine gateway capture. Reference model 3 and the model 6 serving capture are identified separately rather than represented as the same producer.

An isolated non-root container uses one CPU,512 MiB and no network, with input/source hashes unchanged. Three independent direct calculations and 597 permutation statistics agree within 4.44 e-16. Replaying the same audit reproduces its results.

The control is unchanged while current/rotated comparisons are diagnostic shifts. Native marginal PSI also detects changes in this cohort; the run does not establish MMD superiority. The controlled PSI-zero joint-shift example is a separate fixture. No API, automatic retraining or promotion is claimed.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
python -S scripts/verify-joint-cohort.py
python scripts/audit-joint-cohort.py --reference docs/evidence/offline-drift-linux/reference.json --current docs/evidence/private-gateway-linux/feature-refresh/http-all-machines.json --output outputs/joint-cohort-new
```

## Implementation and evidence

- [Source: ml/protocols/joint-cohort-v1.json](../ml/protocols/joint-cohort-v1.json)
- [Recorded artifact: joint-cohort-linux/native.json](evidence/joint-cohort-linux/native.json)
- [Recorded artifact: joint-cohort-linux/container.json](evidence/joint-cohort-linux/container.json)
- [Recorded artifact: joint-cohort-linux/manifest.json](evidence/joint-cohort-linux/manifest.json)
- [Recorded artifact: course-scope/learner-confirmation.json](evidence/course-scope/learner-confirmation.json)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/joint-cohort.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
