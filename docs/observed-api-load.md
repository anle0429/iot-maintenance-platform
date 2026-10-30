# Load test of the observable API wrapper

The native Locust workload exercises the deployed observability wrapper with bounded resources, pacing and recorded replica identities. The two measured runs contain 598 and 5,767 requests, zero failures and P95 latency 6 and 28 ms.

Prometheus/Grafana deltas, Pod state and request ledgers are compared with the client workload. The experiment restores the original replica/HPA settings and leaves the selected model 6 unchanged. Failed admission/Pod attempts remain in the history.

These are measurements at the tested 1/10 user workload, not maximum service capacity or a general SLA for different hardware. Existing coverage/mutation gates establish the wrapper's tested source; this load run is a distinct deployment observation.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
python -S scripts/verify-observed-api-load.py
```

## Implementation and evidence

- [Recorded artifact: observed-api-load-linux/locust-fixed1/users-1.html](evidence/observed-api-load-linux/locust-fixed1/users-1.html)
- [Recorded artifact: observed-api-load-linux/locust-fixed1/users-10.html](evidence/observed-api-load-linux/locust-fixed1/users-10.html)
- [Recorded artifact: observed-api-load-linux/locust-fixed1/verification.json](evidence/observed-api-load-linux/locust-fixed1/verification.json)
- [Recorded artifact: observed-api-load-linux/screenshots/locust-1-user.jpg](evidence/observed-api-load-linux/screenshots/locust-1-user.jpg)
- [Recorded artifact: observed-api-load-linux/screenshots/locust-10-users.jpg](evidence/observed-api-load-linux/screenshots/locust-10-users.jpg)
- [Recorded artifact: observed-api-load-linux/baseline-refresh/verification.json](evidence/observed-api-load-linux/baseline-refresh/verification.json)
- [Recorded artifact: observed-api-load-linux/baseline-refresh/http-all-machines.json](evidence/observed-api-load-linux/baseline-refresh/http-all-machines.json)
- [Recorded artifact: observed-api-load-linux/load-unit.txt](evidence/observed-api-load-linux/load-unit.txt)
- [Recorded artifact: observed-api-load-linux/load-journal.txt](evidence/observed-api-load-linux/load-journal.txt)
- [Recorded artifact: observed-api-load-linux/fixed1-control/hpa-before.json](evidence/observed-api-load-linux/fixed1-control/hpa-before.json)
- [Recorded artifact: observed-api-load-linux/fixed1-control/hpa-fixed.json](evidence/observed-api-load-linux/fixed1-control/hpa-fixed.json)
- [Recorded artifact: observed-api-load-linux/locust-fixed1/hpa-restored.json](evidence/observed-api-load-linux/locust-fixed1/hpa-restored.json)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/observed-api-load.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
