# Novel ideas and coursework scope

The confirmed ML extensions are MMD for joint sensor-distribution changes and OPA/Rego for deployment-policy checks. The learner explicitly confirmed neither specific technique had been covered in class. Controlled and native evidence is linked from their individual reports.

MMD is an offline diagnostic with fixed kernel/permutation policy; it does not imply improved classifier accuracy or automatic retraining. OPA is a CI gate with allowed/denied native cases; it is not claimed as a cluster admission webhook.

The selected submission now includes both final ML and final LLM/agents. The LLM-specific applications have separate native evidence: [OPA capability/budget admission](agent-admission-policy.md) in agent CI and [document-weighted corpus MMD](rag-corpus-drift.md) in RAG CI. Their accepted and intentionally denied releases are preserved; corpus MMD uses four authored documents and is not a calibrated production drift test. Class schedules are supporting context, not proof of what was actually taught, and an idea from another project must not be relabeled as an implemented IoT feature.

## Implementation and evidence

- [Recorded artifact: course-scope/source-summary.json](evidence/course-scope/source-summary.json)
- [Recorded artifact: course-scope/learner-confirmation.json](evidence/course-scope/learner-confirmation.json)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/a395c4ea47b8f8e235556ab93de3d699ed9df18b/docs/novel-ideas.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
