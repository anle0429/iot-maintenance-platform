# Final rubric audit

The selected submission includes **final ML and final LLM/agents**. Native workflows, the requirement-by-requirement reconciliation and the presentation/demo handover are recorded. The runtime remains a retained private lab with the limits described below. Passing an artifact checker does not award a rubric score.

The supplied workbook contains 57 weighted ML requirements and 60 weighted LLM requirements, with 100 source points per sheet. Original workbook cells, merged ranges and reviewed English translations are pinned separately. The [ML tracker](final-rubric-tracker.md), [LLM tracker](final-llm-rubric-tracker.md) and [implementation map](final-audit-matrix.md) retain the requirement-level detail.

## Recorded work

The 1 November data-platform review identified presentation and deliverable gaps outside what the 117-row artifact integrity check proves. [The data review](data-processing-review.md) adds measured generator characteristics, an equivalent-behavior Docker size comparison, paired native Flink captures, original Spark query inspection, an executed schema notebook and stage-specific DP1/DP2 DataHub captures. SQL relationship inspection and the separately run skew benchmark retain their stated limits. Artifact checks do not establish a rubric grade.

| Area | Evidence and its scope |
|---|---|
| Data and ML | Measured large-source Bronze/Silver processing, separate time-aware training data, native feature serving, drift/retraining and CI. [ML tracker](final-rubric-tracker.md) links each requirement to its own run. |
| Inference and agents | [GPU conversations](gpu-agent-routing.md), [direct MCP boundaries](agent-network-isolation.md), [automatic worker scaling](agent-scaling.md), an [executed notebook](agent-notebook.md) and a [matched startup study](agent-warmup.md). The startup results are mixed across roles; the [L26 evidence boundary](agent-warmup.md#l26-evidence-boundary) states that general startup improvement and cloud-cost savings were not established. |
| Knowledge and delivery | [Native RAG governance](rag-orchestration.md), [RAG delivery](ci-rag.md) and [three separate MCP/agent CI jobs](ci-agents.md). These are actual native releases, not YAML-only deployment claims. |
| Testing techniques and design | [Seven original CI/source captures](mcp-quality-evidence.md) show 132 executed tests, fixtures/mocks, boundary partitions, source-bound mutation reuse and the replay property. [Application factory and dependency injection](mcp-application-design.md) share the production construction path. |
| API quality | [MCP quality](mcp-quality.md) retains coverage and the 681/850 mutation result; [GPU adapter checks](gpu-agent-routing.md) retain 116/140, including non-test failures in the denominator. Unchanged-source reuse is explicit; these gates are not rerun for screenshots. |
| Infrastructure captures | [Original Terraform no-op and successful Ansible runtime/cluster role recaps](llm-infrastructure-evidence.md), tied to retained resource and worker identities. |
| Centralized-secret interface | [Native Vault paths, metadata and current version](vault-agent-ui.md), with root operator inspection distinguished from scoped AppRole consumers. |
| Operations and security | [Private agent interfaces and scoped Vault consumers](agent-gateway.md), [native monitoring and real GPU load](llm-monitoring-demo.md), and a [separate policy-frequency window](llm-policy-window.md). Trace-context warnings remain visible. |
| Comparisons | [Model-artifact A/B](llm-model-ab.md) and [agent-configuration A/B](agent-configuration-ab.md) preserve actual replies, denominators, timings and failed predecessors. The small studies do not establish general model quality. |
| Researched extensions | [OPA configuration admission](agent-admission-policy.md) and [document-weighted corpus MMD](rag-corpus-drift.md), with accepted and intentionally rejected native releases. |
| Data contracts | [Separate sample/target tables, stream CI and original GPU answers](assistant-data-validation.md). One factual answer is wrong and remains in the result. Deployed validation rejects the incomplete predictor input. |
| Retrieval HTTPS | [Private CA/SNI verification, native REST/MCP equality and original post-restart Swagger captures](mcp-gateway.md). Both connection boundaries are explicit: browser loopback HTTP over IAP, then verified private HTTPS. |

## Reconciliation and handover

The [implementation map](final-audit-matrix.md) now has one consistent five-column row for each of the 117 original requirements. The audit checks every ID, requirement text and code/evidence link. The [fresh rehearsal](agent-runtime-validation.md) records actual preparation, the original failed conversation, a narrow configuration repair and one accepted coordinator with two checked child histories/four MCP calls. The [operations runbook](operations.md) provides retained-lab execution and recovery commands; the [documentation index](README.md) provides component and source navigation.

Recorded delivery and mentor grading are separate. Each report retains its actual source, workload, failed outcomes and practical limits.

## Verification method

The maintained audit registers **60 read-only artifact checkers**, plus nine separately inventoried native capsules. It checks original rubric wording/weights, source and payload identities and linked evidence. Each checker must cover the requirement it supports; a successful schema check or a 200 page alone is insufficient for a deployment or authenticated interaction claim.

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

[The previous audit page](https://github.com/anle0429/iot-maintenance-platform/blob/f8a64e5dcf5f54a17e5d6a72225a042217524266/docs/final-audit.md) retains the historical test counts, unsuccessful response iterations and earlier open work. Those counts are not added together or presented as the current regression. Original evidence, failed outcomes and native captures have not been translated or rewritten.

[Architecture](architecture.md) · [Demo and recovery](operations.md) · [LLM learning guide](README.md) · [Rubric source](rubric-source.md)
