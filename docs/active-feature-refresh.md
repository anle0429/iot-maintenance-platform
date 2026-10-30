# Fresh feature materialization for the model 6 demo

A demo must use a genuinely fresh native materialization rather than editing preparation timestamps. The recorded refresh materialized 200 Redis keys, created a new immutable seed and retained model 6, its protocol and portable artifact.

Independent inference checks recomputed 195 predictions and verified five stopped-machine 409 responses. The original receipt's success time and one-hour lease describe that run only; they are not perpetual readiness.

The current operator CLI checks resource/session guards, existing cache expiry, sealed source/model identities and available state before preparing another namespace. Reuse after a partial Helm failure does not reset timestamps or materialize twice. Use the demo runbook for a new session/output rather than overwriting historical state.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
python -S scripts/verify-active-feature-refresh.py
```

## Implementation and evidence

- [Recorded artifact: active6-feature-refresh/verification.json](evidence/active6-feature-refresh/verification.json)
- [Recorded artifact: active6-feature-refresh/original-recover-original-on-failure.txt](evidence/active6-feature-refresh/original-recover-original-on-failure.txt)
- [Recorded artifact: active6-feature-refresh/original-failure-scope.json](evidence/active6-feature-refresh/original-failure-scope.json)
- [Recorded artifact: active6-feature-refresh/original-materialization.txt](evidence/active6-feature-refresh/original-materialization.txt)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/active-feature-refresh.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
