# Understanding sensor drift

## Input drift is not accuracy

Input drift means that a measured input distribution differs from a chosen reference. It is not direct evidence that prediction accuracy has decreased. Accuracy requires suitable observed labels and a valid evaluation protocol. Every drift result must identify the reference, sample and metric.

## Marginal and joint changes

A marginal test examines an individual feature. Two sensors can keep similar individual distributions while their relationship changes. The coursework MMD experiment tests this joint-change case on a bounded sensor cohort. Its result is separate from the PSI monitor and must not be described as a fleet-wide guarantee.

## Reference and model identities

A monitoring reference may have a different model identity from the selected serving model. Preserve the reference returned by the drift service. Do not silently relabel a reference when a candidate is trained or promoted.

## Retraining controls

Drift comparison and model replacement are separate actions. Replay guards and cooldowns prevent duplicate retraining requests. A completed candidate-training pipeline does not establish automatic production promotion. The assistant's tools are read-only; it cannot start retraining, change thresholds or replace a serving model.
