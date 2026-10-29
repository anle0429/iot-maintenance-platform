# Boundary, partition and property-based API validation

The focused native validation package tests behavioral partitions and boundaries of the feature, predictor, drift and A/B APIs. It includes malformed/unknown IDs, operating-state eligibility, timestamp/freshness boundaries, invalid numeric inputs, model/protocol mismatch and replay/conflict cases.

The recorded Linux run passed 71 JUnit test cases and 220 Hypothesis examples across 14 groups, with 31 Git inputs bound to the run. Property-based generation searches for order, duplication and replay combinations that could violate the contract; it is not a replacement for known-answer fixtures.

Original console, JUnit, source bindings and screenshots are retained. This package does not claim a new deployment, coverage run, mutation run or throughput benchmark. Those are independently documented gates.

## Implementation and evidence

- [Recorded artifact: api-validation-linux/manifest.json](evidence/api-validation-linux/manifest.json)
- [Source: tests/api/focused-validation.json](../tests/api/focused-validation.json)

## Recorded interface views

These are original captures from the runs described above. Their timestamps, model identities and visible states belong to those runs.

![Recorded boundaries](evidence/api-validation-linux/screenshots/boundaries.jpg)

![Recorded hypothesis](evidence/api-validation-linux/screenshots/hypothesis.jpg)


## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](demo-walkthrough.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/api-validation.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
