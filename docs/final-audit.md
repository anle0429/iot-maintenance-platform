# Final rubric audit

The selected submission includes **final ML and final LLM/agents**. The full coursework remains incomplete. Native workflows have recorded evidence; the remaining requested captures and final row-by-row reconciliation are listed below. Passing an artifact checker does not award a rubric score.

The supplied workbook contains 57 weighted ML requirements and 60 weighted LLM requirements, with 100 source points per sheet. Original workbook cells, merged ranges and reviewed English translations are pinned separately. The [ML tracker](final-rubric-tracker.md), [LLM tracker](final-llm-rubric-tracker.md) and [implementation map](final-audit-matrix.md) retain the requirement-level detail.

## Recorded work

| Area | Evidence and its scope |
|---|---|
| Data and ML | Measured large-source Bronze/Silver processing, separate time-aware training data, native feature serving, drift/retraining and CI. [ML tracker](final-rubric-tracker.md) links each requirement to its own run. |
| Inference and agents | [GPU conversations](gpu-agent-routing.md), [direct MCP boundaries](agent-network-isolation.md), [automatic worker scaling](agent-scaling.md), an [executed notebook](agent-notebook.md) and a [matched startup study](agent-warmup.md). The startup results are mixed across roles. |
| Knowledge and delivery | [Native RAG governance](rag-orchestration.md), [RAG delivery](ci-rag.md) and [three separate MCP/agent CI jobs](ci-agents.md). These are actual native releases, not YAML-only deployment claims. |
| API quality | [MCP quality](mcp-quality.md) retains coverage and the 681/850 mutation result; [GPU adapter checks](gpu-agent-routing.md) retain 116/140, including non-test failures in the denominator. Unchanged-source reuse is explicit; these gates are not rerun for screenshots. |
| Operations and security | [Private agent interfaces and scoped Vault consumers](agent-gateway.md), [native monitoring and real GPU load](llm-monitoring-demo.md), and a [separate policy-frequency window](llm-policy-window.md). Trace-context warnings remain visible. |
| Comparisons | [Model-artifact A/B](llm-model-ab.md) and [agent-configuration A/B](agent-configuration-ab.md) preserve actual replies, denominators, timings and failed predecessors. The small studies do not establish general model quality. |
| Researched extensions | [OPA configuration admission](agent-admission-policy.md) and [document-weighted corpus MMD](rag-corpus-drift.md), with accepted and intentionally rejected native releases. |
| Data contracts | [Separate sample/target tables, stream CI and original GPU answers](assistant-data-validation.md). One factual answer is wrong and remains in the result. Deployed validation rejects the incomplete predictor input. |
| Retrieval HTTPS | [Private CA/SNI verification, native REST/MCP equality and original post-restart Swagger captures](mcp-gateway.md). Both connection boundaries are explicit: browser loopback HTTP over IAP, then verified private HTTPS. |

## Remaining deliverables

- L27–L30: requested screenshots showing coverage, fixtures/mocks, parameterized boundary/partition cases, mutation and property-based techniques. Native test results already exist; the missing images must show those actual results and code.
- L48–L49: requested Terraform and Ansible run captures, tied to the retained CPU/GPU resources and agent deployment.
- L58: a native Vault UI capture that demonstrates centralized shared secrets without exposing values.
- L59: a capture and explanation of an implemented LLM application pattern, such as the MCP application factory and dependency injection.
- Reconcile all original requirements against current sources, evidence and delivery files, then complete the presentation/demo handover. Do not infer completion from the number of green checks.

## Verification method

The maintained audit registers **55 read-only artifact checkers**, plus nine separately inventoried native capsules. It checks original rubric wording/weights, source and payload identities and linked evidence. Each checker must cover the requirement it supports; a successful schema check or a 200 page alone is insufficient for a deployment or authenticated interaction claim.

The [retrieval HTTPS checker](../scripts/verify-mcp-gateway.py) authenticates 198 original native payloads, 24 historical source bindings, 13 HTTP outcomes, three matching MCP tool results and two original interface captures. Screenshot response values and raw-body digests are reconciled with the native TLS audit. The original failed deployments and the earlier checkpoint metadata remain intact.

Historical source reads now use a single binary-framed Git process per manifest. The reader checks object type, byte length and Git object identity. Real-repository tests cover binary/empty/Unicode source, edited working files, a moved branch and invalid selections. This reduces repeated process startup on Windows while preserving the historical byte and SHA-256 checks. It is an audit implementation improvement, not an LLM benchmark or another novelty claim.

Run from the repository root with a fresh output path:

```powershell
$tag = Get-Date -Format 'yyyyMMdd-HHmmss'
python -B -S scripts/audit-final-coursework.py --output "outputs/audit-$tag" --workers 1
```

Read the resulting JSON and individual command outputs. The report deliberately keeps `coursework_complete=false`: artifact integrity, current service readiness, complete rubric delivery and grading are different claims. No service or cloud resource starts when running this audit.

## Issues corrected during the ML review

Historical host-verifier checks permit documentation-only edits while retaining immutable Git bindings. A clean Windows clone exposed a CRLF-derived source pin; the canonical LF Git blob is now pinned. Native recovery restores transient monitoring, relays, collectors, Jenkins and Vault consumers. Separate scoped credential identities and original file permissions are preserved.

Real Feast preparation refreshes expired features instead of changing timestamps. TLS renewal issues actual certificates with protected existing keys and retains the Vault identity. The earlier after-restart package records materialization for 200 machines, nine HTTP cases, the Vault CI success/cross-service/revoked-token checks, four CA/SNI routes and reachable native pages. A page returning 200 is kept separate from authenticated dashboard or successful DAG evidence.

## Practical limits

The 100 GB workload covers Bronze/Silver; model development uses a separate source. Two distributed executors share one physical CPU VM. Gateways use private lab domains and a private CA. Service-mesh tests are scoped to their namespace. Stream CI exercises retained finalized windows, not a continuously operating stream. The reference corpus and pump data are authored/simulated; model scores are not certified failure probabilities for industrial equipment.

Native runs and local checks have their own machine/source identities. A clean PC clone does not establish laptop execution or a fresh Linux deployment. Feature preparation uses a genuine one-hour wall-clock lease; a recorded successful response is not proof that cached data remains available later. GPU aliases require explicit loading after an actual GPU restart.

## Earlier checkpoints

[The previous audit page](https://github.com/anle0429/iot-maintenance-platform/blob/078adc9d66fcee406a190eedc31f565471e36d55/docs/final-audit.md) retains the historical test counts, unsuccessful response iterations and earlier open work. Those counts are not added together or presented as the current regression. Original evidence, failed outcomes and native captures have not been translated or rewritten.

[Architecture](architecture.md) · [Demo and recovery](demo-walkthrough.md) · [LLM learning guide](llm-learning-guide.md) · [Rubric source](rubric-source.md)
