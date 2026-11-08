# Admit agent capabilities before deployment

The production agent pipelines evaluate their complete agent configuration with **OPA/Rego before any deployment command**. A second check binds deployment to the same rendered manifest, live model identity and approved input. The policy covers tools, delegation, model routing, generation budgets, worker isolation and the two retained network policies.

## Current Agentgateway proof — 8 November 2026

The selected **thirteen-resource Agentgateway configuration** now has a fresh native OPA suite: **79 cases, four allowed and 75 denied**. Each case retains its actual input, explicit decision, process exits and source/binary hashes. The process ledger reconciles **160 pinned OPA commands**: one strict check, one version query and two evaluations per case. This records policy-process dispatch; independent CI observations below establish the deployment boundary.

| Coordinator build | Controlled scenario | Actual result | Independently checked outcome |
|---|---|---|---|
| **19** | Add unapproved `start_pump` | `FAILURE` at OPA | `allowed: false`, violation names `SandboxAgent/iot-retrieval`, gate exit **1**; deployment and answer-verification stages skipped |
| **18** | Production configuration | `SUCCESS` | All source-declared stages and native containers succeeded; three MLflow prompts pinned to v1; exact manifest/input/model-UID recheck; both actual child answers verified after Helm reapplication |

For build **19**, the original output directory and Jenkins artifact index both contain **no deployment plan, deployment receipt or deployment-attempt marker**, and no answer-verification receipt/attempt. Independent before/after snapshots preserve identical UIDs and complete specifications for **27 resources**: five agents, three models, six MCP server references, three worker pools and ten Deployments. These namespace snapshots include retained resources beyond the thirteen-object policy input. All **three Helm revision readbacks** are identical. The native GPU output-token counter remains **2278 → 2278**, a measured delta of **0.0** in this isolated observation window.

Both CI builds use source commit `ad2da29262e3088c942a9240d7157708c094aca4`, with **402 sealed inputs**. The policy suite preserves its own earlier as-run source seal. The [new native archive](../evidence/agent-delegation-20261108/native-receipts.tar.gz), [capture receipt](../evidence/agent-delegation-20261108/native-archive.json) and [independent verifier](../../scripts/verify-agent-delegation-review.py) retain and recompute these facts. Relevant archive paths are `policy/`, `ci/denied-c/`, `ci/build-19/`, `ci/allowed-b/`, `ci/build-18/` and `source-ci/`.

Earlier attempts remain visible: builds **15 and 16 failed before OPA** because the policy test container lacked the Helm executable; a regression test then exposed and fixed its missing readonly mount. Build **17 reached a real OPA denial**, but its after-snapshot was overwritten by a later observation after build 18. It is **not** used for an independent no-change or zero-token claim. Fresh build **19** supplies that evidence. The original seventy-case proof below is unchanged.

## Original direct-route proof — 30 October 2026

The retained coordinator Jenkins build **4** demonstrates an actual blocked release: add the unapproved `start_pump` tool → OPA returns `allowed: false` with `unapproved capability or missing required field: SandboxAgent/iot-retrieval` → the gate exits **1** → deployment and answer verification are skipped. Resource UIDs/specifications, Helm revisions and the GPU output-token counter remain unchanged. The [original negative CI report](../evidence/agent-admission/negative-ci-verification.json) and the checks below preserve this complete chain.

The original **30 October 2026** verification covers **70 known-answer cases** and **four actual Jenkins builds**. Four valid configurations passed; 66 invalid configurations were denied. Coordinator, retrieval and drift build **5** each passed admission, deployment and real answer verification. These are the original eleven-resource, direct observer-route runs; the later Agentgateway evidence is distinguished below.

## Unsuitable configurations that OPA actually denied

These examples come from the retained native inputs and OPA outputs, rather than expected test results alone. In every row, `decision.stdout` contains `allowed: false` and the violation `unapproved capability or missing required field: <object>` for the object shown; `gate-exit.json` contains `exit_code: 1`.

| Original case | Actual candidate change | Object named in the OPA violation | Gate exit |
|---|---|---|---|
| `unsafe-hardware-tool` | Append `start_pump` to the retrieval tool list | `SandboxAgent/iot-retrieval` | **1** |
| `external-model-destination` | Set `baseUrl` to `https://unapproved.example.invalid/v1` | `ModelConfig/default-model-config` | **1** |
| `over-output-budget` | Set `maxTokens` to `769`, above the approved maximum `768` | `ModelConfig/default-model-config` | **1** |
| `shared-delegation-session` | Set the retrieval delegation's `isolateSessions` to `false` | `SandboxAgent/iot-coordinator` | **1** |
| `privileged-worker-template` | Add `securityContext: {privileged: true}` to the worker template | `WorkerPool/iot-retrieval-pool` | **1** |
| `changed-network-policy` | Set the retained actor-MCP policy's `egress` to `[{}]` | `NetworkPolicy/iot-agent-actor-mcp` | **1** |

The [70-case native report](../evidence/agent-admission/policy-contracts-verification.json) records each actual decision, violation, gate exit and input SHA-256. The unchanged [native archive](../evidence/agent-admission/native-receipts.tar.gz) retains each case's `input.json`, `decision.stdout`, `gate.stdout` and `gate-exit.json` under `agent-admission-capture-30a/native/agent-admission-contracts-30b/<case>/`. The [independent verifier](../../scripts/verify-agent-admission.py) authenticates those bytes and compares them with the declared cases. This policy-only suite makes no deployment or model request; the following Jenkins run separately proves the deployment boundary.

For Jenkins coordinator **4**, the verifier checks the exact same `unsafe-hardware-tool` input and confirms that no `policy.json`, `deployment-plan.json`, `deploy.json`, `verify.json` or `admitted-deploy-host.attempt` was produced. It compares the before/after UIDs and complete specifications of **22 resources** across agents, models, MCP servers, worker pools and Deployments; all three Helm revision readbacks are equal. The independent native GPU counter delta is **0.0**, and the original console explicitly says both downstream stages were skipped. A pipeline failure alone would not establish these outcomes.

![Original coordinator stage view: accepted build 5 and intentionally denied build 4](../evidence/agent-admission/ui/coordinator.jpg)

## Why this is useful

A correct tool name is only part of the configuration. An agent could still point at an unapproved MCP server, bypass the observed model route, share a delegated session or enable code execution. The structured policy checks these relationships together and returns explicit violations for CI review.

The learner confirmed OPA/Rego had not been covered in class. This LLM application evaluates the actual production agent chart and live shared model. The earlier ML deployment-policy experiment remains separate evidence. The official [OPA CI/CD guide](https://www.openpolicyagent.org/docs/cicd) describes configuration checks and command exit codes; this bounded CLI gate needs no additional policy server.

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="../images/agent-admission.png">
  <img alt="Source-bound agent release admission before deployment" src="../images/agent-admission.gif">
</picture>

[Editable Excalidraw](../diagrams/agent-admission.excalidraw) · [Full-resolution PNG](../images/agent-admission.png) · [Interactive viewer](../diagrams/architecture.html)

Moving markers illustrate direction with arbitrary timing. Reduced-motion visitors receive a static image.

## What it checks

The original direct-route input contains three `SandboxAgent` objects, three `WorkerPool` objects, two `RemoteMCPServer` objects, two `NetworkPolicy` objects and the live shared `ModelConfig`: **eleven resources**. Missing, duplicated, additional or unrecognized objects fail the declared production inventory.

The current [Agentgateway overlay](../../infra/agentgateway/agents-default.yaml) selects a **thirteen-resource** configuration: the same inventory plus two gateway MCP references, with the shared model and selected specialist tools routed through Agentgateway. The [7 November gateway report](../evidence/mentor-runtime-20261107/gateway-default.json) and [coordinator build 14 policy receipt](../evidence/mentor-runtime-20261107/ci-policy.json) retain their earlier allow-only scope. The new 8 November suite and build 19 above supply separate current-route denial evidence. The original 66 denials and Jenkins build 4 retain their direct-route scope.

| Area | Reviewed constraint | Rejected example |
|---|---|---|
| Model route | Existing private observer/router endpoint and shared 7B model | External endpoint or direct route bypassing the observer |
| Generation budget | Integer output budget from 1 to 768; positive timeout up to 600 seconds | 769 tokens, zero timeout or Boolean/string token count |
| Credentials | Existing Secret name/key reference | Missing reference or inline credential field |
| Tool access | Exact per-role read-only tool set and MCP reference | Added `start_pump`, unknown server or execution tool |
| Delegation | Two declared specialists with isolated sessions | Shared sessions or cross-namespace delegation |
| Agent behavior | Reviewed prompt/version, Go runtime, streaming and disabled code blocks | Replaced prompt or enabled code execution |
| Worker isolation | Retained gVisor image/configuration, limits and two-worker baseline | Unreviewed runtime/image, more baseline workers or privileged template |
| Network | Approved destinations and retained policy specs | Additional domain or open egress rule |

Read the [Rego rules](../../infra/policy/agent-admission.rego), [approval data](../../infra/policy/agent-admission-approved.json), [known-answer cases](../../agents/admission_cases.py) and [observed baseline](../../tests/fixtures/agent-admission-observed.json). Changing capabilities requires a reviewed policy/data change; a model answer cannot grant itself another tool.

The optional A/B pair is accounted for separately. Production readiness checks the three production roles, rejects unknown/incomplete/duplicate extra identities, and compares both A/B agents' and model configurations' UIDs/specs before and after deployment.

## Real CI outcomes

| Job and build | Scenario | Jenkins result | Verified outcome |
|---|---|---|---|
| Coordinator 4 | Synthetic additional hardware tool | `FAILURE` | OPA false/exit1; no deployment attempt or plan; unchanged resource UIDs/specs and Helm revisions; zero new GPU output tokens |
| Coordinator 5 | Production | `SUCCESS` | Admission, exact-input recheck, rollout and verified specialist delegation |
| Retrieval 5 | Production | `SUCCESS` | Admission, recheck, actual MCP rollout and tool-grounded risk/reference answer |
| Drift 5 | Production | `SUCCESS` | Admission, recheck, actual MCP rollout and tool-grounded drift answer |

All four builds use commit `beb7f5c7dbec14e8429a0877c87eb17e6c901b7b`, with **375 sealed inputs**. The [pipeline](../../infra/ci/AgentsPolicy.Jenkinsfile) and [controller adapter](../../scripts/ci-agents-policy.py) reuse the retained test/build/deployment/rollback machinery. The original pipeline and historical source-bound evidence remain intact.

The production builds verify four specialist answers: two coordinator children and two direct requests. Their eight actual MCP tool events succeeded. Both retrieval predictions match independent arithmetic against portable model 6, with maximum score error **0.0**. This is a controlled synthetic lab demonstration.

![Original successful retrieval pipeline](../evidence/agent-admission/ui/retrieval.jpg)

![Original successful drift pipeline](../evidence/agent-admission/ui/drift.jpg)

The red downstream boxes in coordinator build 4 inherit the earlier failure. The console explicitly records deployment and answer verification as skipped; native resource/revision checks confirm no changes. Older rows visible in these views belong to their original runs.

![Original console confirming deployment was skipped](../evidence/agent-admission/ui/negative-console.jpg)

## An integration error caught during development

The first native suite exposed a distinction: `allowed == true` can return a **defined false value** while `opa eval --fail` still exits zero. The wrapper compared decision and exit code and stopped before deployment. Process success did not become permission.

The corrected gate uses the truth constraint `allowed = true`: denial then produces an empty query result and exit1. The [official CLI reference](https://www.openpolicyagent.org/docs/cli#opa-eval) defines `--fail` in terms of an empty/undefined result or an error. Original failed-suite records and the later 70-case pass are retained.

The package also retains the original Jenkins XML preflight failure and the later scoped update. An explicit Vault root/unseal scan supplements the initial credential scan without rewriting execution files. Credentials, weights, bulk data and image-export archives are excluded from Git.

## Verify or demonstrate it yourself

From the repository root, without a running VM:

```powershell
python -B -m unittest tests.test_agent_admission tests.test_agent_admission_ci -v
python -B -S scripts/verify-agent-admission.py
python -B -S scripts/verify-agent-delegation-review.py
```

The original verifier reads archives in memory, avoiding Windows path-length limits. It authenticates **1,833 original native payloads**, checks every policy input/decision/exit, reconciles the denied deployment boundary, verifies each production phase and checks actual answers and prediction arithmetic. The new verifier separately checks the current source-bound policy suite, complete Jenkins artifact inventories, raw GPU counters and actual delegated child histories/transport. These offline checks start no service and spend no cloud credit.

For a fresh policy-only demonstration on the existing CPU VM, use the retained authenticated source and a new output directory:

```bash
sudo /usr/bin/python3 -B \
  /srv/iot-data/workspace/iot-maintenance-platform/data/llm-source/agent-admission-727cb6db/tree/scripts/check-agent-admission.py \
  --opa /srv/iot-data/workspace/iot-maintenance-platform/data/runtime/policy-assets-28/opa \
  --source-manifest /srv/iot-data/workspace/iot-maintenance-platform/data/llm-source/agent-admission-727cb6db/manifest.json \
  --output /srv/iot-data/workspace/iot-maintenance-platform/outputs/agent-admission-demo
```

This needs the CPU lab and pinned OPA binary; it makes no model request and deploys no candidate. Keep existing output and choose a new name when repeating. The complete production pipeline additionally needs a genuinely fresh feature lease and sufficient time in both VM sessions.

Inspect the retained private UI at [coordinator](http://127.0.0.1:19787/job/iot-llm-coordinator/), [retrieval](http://127.0.0.1:19787/job/iot-llm-retrieval/) and [drift](http://127.0.0.1:19787/job/iot-llm-drift/). VM/tunnel availability is bounded; offline evidence remains usable after they stop.

## Scope and limits

The gate protects these owned CI procedures. It is not a Kubernetes admission webhook and cannot constrain a separate administrator using `kubectl` directly. It checks configuration boundaries; prompt admission and runtime tool validation remain separate controls. The immediate recheck catches observed input changes before deployment, but does not create an atomic transaction with all concurrent cluster administrators.

The original eleven-object input and current thirteen-object gateway input cover the selected agent chart and live model. They do not independently certify every MCP application image or all Kubernetes resources. Existing API tests, image/source binding and the unchanged strict mutation baseline retain their own scope.

For a presentation, show the rejected `start_pump` case first, then a green production row. Explain why the **decision, exit code, exact configuration identity and actual outcome** all matter.
