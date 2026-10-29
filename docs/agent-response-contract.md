# Complete agent answers

The first native agent conversations exposed two separate limits: a 4,096-token context cut off one answer, and a later 384-token output budget was spent on a feature table before the answer reached the risk score. Increasing context alone did not solve the second problem. A completed A2A task is therefore checked against its actual tool results before it is accepted.

**Current status:** response policy `0.2.0` and its guarded rollout are prepared and tested locally. They have not been deployed or accepted on the VM. The recorded native deployment remains `0.1.0`; its original source and evidence are preserved.

## What the last native run proves

The [original response baseline](evidence/agent-response-baseline/manifest.json) retains the complete chat phase from the final captured VM run. Every selected native file is byte-identical to the original archive and capture inventory. Unrelated earlier phases remain in the retained archive and the separate [foundation report](agent-platform.md).

| Result | Observed outcome |
|---|---|
| Retrieval | Three genuine tool results; A2A completed in 339.22 seconds. Final answer used 384 tokens and omitted the risk score. Rejected as a complete answer. |
| Drift | A2A completed in 716.98 seconds. Final text preserved 32 unique machines, six flagged features, window/reference/assessment identities and the accuracy/failure limitation. |
| Drift retries | Three equal successful tool results and one argument error. These remain 32 unique machines. The error is retained. |
| Coordinator | Card and request retained; no final response. |
| Whole run | The bounded process timed out after 1,400 seconds. Full conversation acceptance remains open. |

The provider's `adk_partial` metadata is true in both specialist responses. That flag alone cannot establish whether the final answer is incomplete: inspect its facts, ending and tool results. The drift answer is complete for its requested facts, while the retrieval answer omits a required value.

## The candidate response policy

[Versioned role prompts](../agents/response-policy.json) ask specialists for compact JSON. Retrieval returns the machine, feature timestamp, model version, risk score, document IDs and explicit simulation/calibration/vibration limitations. Drift returns the window, unique count, flagged feature names, reference and assessment IDs, and interpretation limits. The coordinator delegates to both specialists and preserves their objects.

The candidate [budget overlay](../infra/agents/conversation-budget.yaml) allows 768 output tokens and 1,800 seconds for a delegated A2A call, with the existing 600-second model timeout. These are bounded allowances, not measured speed improvements. They do not change the model, CPU, RAM, disks, tools, network destinations or data lease. Actual latency and full-answer acceptance must be measured after deployment.

The [post-renderer](../scripts/render-agent-responses.py) changes only the three owned agents' prompt, version and policy-hash annotation. A test compares every other object field with the original native resources. The [rollout operator](../scripts/configure-agent-conversations.py) requires an unchanged sealed source, 75 approved session minutes, 16 GiB available RAM and 25 GiB free SSD. It refuses an active chat job, verifies the internal endpoint, substrate TLS and at least 8,192 actual context tokens, then waits for Accepted/Ready conditions at the new resource generation. Its two Helm upgrades are individually atomic; they are not one cross-release transaction.

The [catalog publisher](../scripts/publish-iot-agents.py) now uses the version actually observed in the agent resource. It rejects an existing tag bound to another resource/configuration. A future `0.2.0` publication must preserve the recorded `0.1.0` entry.

The [native client](../agents/probe.py) uses short identity/card checks and separate bounded waits for specialist and coordinator responses: 1,200 and 2,100 seconds. A network timeout or incomplete HTTP body produces a failed transport receipt, retaining any partial bytes without creating a complete response. The original baseline client source remains in each capture; the verifiers report whether it matches the current client. These transport changes have local tests and still require a new native run.

## How acceptance works

The [specialist checker](../agents/answer_acceptance.py) reads the final artifact rather than searching prompts/history for a convenient value. It pairs successful native tool outputs with call IDs, checks the requested machine/cohort, rejects contradictory snapshots, validates citation bytes against authored sources, and compares the answer's values with the actual results. Repeated equal drift returns are not added to the cohort count. Failed tool events remain counted even when a retry recovers.

Run the original-baseline verifier on a normal PC without starting services:

```powershell
python -S scripts/verify-agent-response-baseline.py
```

After a future native policy 0.2 run, check each original response separately:

```powershell
python scripts/check-agent-answer.py outputs/your-new-run/retrieval-response.json --role retrieval
python scripts/check-agent-answer.py outputs/your-new-run/drift-response.json --role drift --window-id agent-demo-29
```

Replace `your-new-run` with the actual new output directory. The compact checker intentionally rejects the older prose answers; it does not rewrite them. The historical drift facts are checked separately by the baseline verifier. Passing the compact checker proves agreement with the supplied captured tool events, not the authenticity of an arbitrary file or coordinator delegation. Native source seals, transport/task identities and child-delegation traces are still required.

Run the local checks:

```powershell
python -m pytest tests/test_agent_answer_acceptance.py tests/test_agent_response_renderer.py tests/test_agent_conversation_rollout.py tests/test_agent_response_baseline_evidence.py -q
```

## Next native acceptance

Use an approved VM session and a fresh sealed source. Apply the candidate with the rollout operator, genuinely prepare a new feature snapshot, then capture complete retrieval, drift and coordinator conversations. Republish the observed version after its current generation is Ready. Preserve source/model/policy identities, elapsed time, tool calls/errors and final artifacts. A feature lease that expires during the test is a real failure to resolve; do not edit its preparation time or TTL.

The coordinator still needs direct evidence that it delegated to both specialists and preserved their actual answers. Sandbox allow/deny probes, autoscaling, a cold/warm comparison, notebooks, CI, gateway controls, telemetry, A/B and the failed mutation gate remain separate work.
