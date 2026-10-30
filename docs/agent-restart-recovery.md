# Recovering the retained agent lab

The 30 October restart exposed several dependencies that a healthy Kubernetes node alone does not establish: cluster member addresses, worker registrations, free CPU reservations, a valid feature lease and ready MCP endpoints. The recovery restored the existing lab and preserved its data and failed-run receipts. It did not provide uninterrupted service or cross-host high availability.

## What the restart revealed

| Observation | Recorded action and result |
|---|---|
| MLflow initially exceeded the startup read timeout | The failed attempt was kept; the next recovery verified model 6 and all six service health endpoints |
| Valkey members still advertised old Pod IPs | Reconnected the six retained IDs; a real automatic failover occurred, then a separate check accepted the converged cluster with all 16,384 slots |
| KEDA fallback requested four workers per pool while its metrics source was unavailable | Temporarily held the existing two-worker baseline, then restored the original policies and annotations |
| An idle CPU model reserved 1.5 CPU and 4 GiB despite the selected GPU route | Parked only its replica count at zero after checking the GPU route and idle CPU slots |
| Six worker registrations retained old addresses | Replaced only the six idle owned worker Pods and verified the new registrations against their actual Pod IPs |
| Two retrieval MCP Pods remained unavailable after reboot | Retained their original state, restarted the same application release and verified two ready replicas |

The [native evidence inventory](evidence/llm-agent-ab/manifest.json) keeps the original failed and successful attempts. The [offline checker](../scripts/verify-llm-agent-ab.py) verifies the relationships between their source, state and results.

## Session and credential boundaries

The approved CPU session started at 00:56:28 UTC and retained its eight-hour automatic STOP. The [resumer](../scripts/resume-demo-services.py) derives the deadline from an actual Compute descriptor. Editing the deadline file is not a substitute for starting an approved session.

The old metrics collector and the newer LLM discovery reader use different Kubernetes service accounts. Recovery now validates each retained subject before renewal and preserves its file owner, group and mode. In particular, the LLM reader retains its group-readable credential file. No bearer value is included in the receipts.

Vault was unsealed using its existing Raft storage. It was not reinitialized. The existing CI and gateway AppRole permissions were preserved while their short-lived consumer credentials were renewed.

## Cluster membership and worker addresses

The [member recovery operator](../scripts/restore-substrate-members.py) first requires the same six local node IDs, owned PVC bindings and consistent complete slot/replica assignments. It changes address announcements and reconnects members; it does not flush, reset or reshard application data.

During the first recovery, a replica was automatically promoted at epoch 9. The strict post-check refused the changing topology, so that attempt remains failed. The subsequent check accepted the converged three-primary/three-replica arrangement with the same six IDs. Its preserved assignment refers to that later stable check; the role assignment did change during the first attempt.

Healthy cluster membership did not fix the separately retained worker addresses. The [worker recovery operator](../scripts/restore-substrate-workers.py) checks the six owned Pods and refuses running actors before replacing idle workers. Its accepted result matches every registered address to a current Pod IP. Actor histories and snapshot storage were not reset.

## Release CPU and restore scaling

The [CPU standby operator](../scripts/park-cpu-model.py) verifies the accepted GPU adapter/observer specifications, loaded model aliases and shared 7B selection before parking the idle legacy Deployment. Its Service, weights and configuration remain available. To restore the CPU prototype when needed:

```bash
sudo k3s kubectl scale deployment/iot-model -n iot-llm --replicas=1
```

That requires room for its existing 1.5-CPU/4-GiB reservation. The GPU route remains the selected agent path.

KEDA's [documented maintenance pause](https://keda.sh/docs/2.21/concepts/scaling-deployments/) temporarily held two workers per pool. The original scaler UIDs, specifications and annotations were saved first. After recovery, only the temporary pause annotations were removed. The first observer timed out while native Paused conditions remained True; a KEDA operator restart then reconciled all three conditions to False. No status field was manually edited. Three actual HPAs again reported scaling active, with the original two-to-four limits.

## Reuse valid preparation; verify the complete path

The original SDK preparation measured zero matching Redis keys before materialization and 200 afterwards. Its first Helm upgrade and rollback both timed out under CPU starvation. Once capacity recovered, `--resume-deploy` reused the exact prepared files and immutable seed after checking their hashes, age and physical lease. It did not rerun preparation or extend expiry.

The lease remained 01:01:53–02:01:53 UTC. A later demonstration needs another genuine preparation once that lease expires. The [agent comparison](agent-configuration-ab.md) ran within the accepted lease and independently checked its prediction scores.

Before another live question, check the controller, current worker addresses, both ready MCP replicas, feature readiness and the actual lease. The first comparison's HTTP 200 responses still contained failed tasks because retrieval MCP was unavailable. Native task completion and tool-grounded answer checks are required after transport succeeds.

The GPU two-model router retains explicit loading in its accepted configuration. After a later GPU restart, load both preset aliases and wait until `/models` reports `loaded`; a Ready router alone does not prove its child models are ready. Historical completed experiment gauges may be restored after a Pushgateway restart, but their execution time and original payload must remain unchanged.
