# Native agent control plane and catalog

The latest recorded specialist chats appear first below. The earlier [GPU agent checkpoint](gpu_agent_routing.md) records complete specialist answers, actual coordinator delegation and catalog version 0.2.2. The original deployment and failure baseline remains preserved later on this page.

## Native specialist tools

A new coordinator conversation on **8 November 2026 UTC** completed with two genuine child sessions and **four successful MCP calls**. Retrieval executed `get_machine_features`, `predict_failure` and `search_knowledge`; drift executed `assess_feature_drift`. The original native screenshots below show expanded tool arguments and returned results in those child chats.

| Conversation | Context / child session ID |
|---|---|
| Coordinator parent | `ed943603-418d-4269-9b0c-0d177a878244` |
| Retrieval child | `01a1ba15-99e7-748b-8624-34298460e267` |
| Drift child | `01a1ba15-bbdf-7013-9fb0-3af972877a14` |

The parent's original delegation results identify these two child sessions. Content acceptance checks their completed tasks, paired tool calls/results and preservation in the combined answer. Separate transport acceptance joins both child A2A requests to the same parent/root context and the actual coordinator worker Pod IP and UID. The parent entered through the authenticated private UI gateway; its two internal delegations traversed Agentgateway. [Current route and transport checks](global_model_config.md#native-results) · [Original parent Activity capture](../evidence/agent-delegation-20261108/ui/parent-retrieval-activity.jpg).

**Retrieval: actual P-001 request and feature result.** The same child also obtained the model-6 prediction and the versioned vibration passage.

![Original retrieval child get_machine_features call with expanded P-001 arguments](../evidence/agent-delegation-20261108/ui/retrieval-feature-call.jpg)

![Original retrieval child get_machine_features call with expanded returned features](../evidence/agent-delegation-20261108/ui/retrieval-feature-result.jpg)

[Prediction arguments](../evidence/agent-delegation-20261108/ui/retrieval-prediction-expanded.jpg) · [Prediction result](../evidence/agent-delegation-20261108/ui/retrieval-prediction-result.jpg) · [Knowledge request and returned source passage](../evidence/agent-delegation-20261108/ui/retrieval-knowledge-arguments.jpg).

**Drift: actual cohort request and comparison result.** The captured request uses the standard 32-machine cohort and `agent-demo-29` window; the returned result retains the reference and assessment identities.

![Original drift child assess_feature_drift call with expanded cohort arguments](../evidence/agent-delegation-20261108/ui/drift-arguments.jpg)

![Original drift child assess_feature_drift call with expanded returned comparison](../evidence/agent-delegation-20261108/ui/drift-result.jpg)

These unedited JPEGs show completed tool cards. Long arguments and results occupy the native interface's scrollable panels, so a screenshot shows only the visible portion. The [review index](../evidence/agent-delegation-20261108/review-index.json), [verification](../evidence/agent-delegation-20261108/verification.json) and [original native archive](../evidence/agent-delegation-20261108/native-receipts.tar.gz) preserve the complete task histories, call pairing and image identities. This is one recorded conversation on simulated data; the fixed drift window can reuse an existing assessment. It establishes neither a new drift window nor continuous service availability.

## Recorded specialist MCP execution — 30 October checkpoint

These original screenshots were captured on **30 October 2026 at 06:41 UTC**, after an accepted coordinator conversation completed. They show the actual retrieval and drift child **Activity** panels inside that coordinator chat. The [post-restart validation](agent_runtime_validation.md) provides the independent receipt checks, original failure and bounded runtime scope for this recorded conversation.

Retrieval shows **three successful MCP calls**: `get-machine-features`, `predict-failure` and `search-knowledge`, followed by its returned answer for P-001.

![Original retrieval child Activity panel with three successful MCP calls and its answer](../evidence/learner-demo/ui/accepted-retrieval.jpg)

Drift shows **one successful MCP call**, `assess-feature-drift`, followed by its returned assessment for the 32-machine cohort and `agent-demo-29` window.

![Original drift child Activity panel with one successful MCP call and its answer](../evidence/learner-demo/ui/accepted-drift.jpg)

The screenshots show completed tool summaries; the individual per-call arguments and results remain collapsed. The UI displays hyphenated tool names, while the native histories use `get_machine_features`, `predict_failure`, `search_knowledge` and `assess_feature_drift`.

The accepted parent and its independently checked children retain these original identities:

| Role | Task ID | Context / child session ID |
|---|---|---|
| Coordinator | `01a18b7f-929f-701e-81c4-cf6578af2253` | `ab61aaf9-65ef-414e-9d1f-4fcdd8fa0c13` |
| Retrieval child | `01a18b7f-a531-73ef-a143-48170d2f08cc` | `01a18b7f-a28a-79c3-927d-0bd90c97e2a1` |
| Drift child | `01a18b7f-f7fb-7b00-a9d0-f826252f34bc` | `01a18b7f-f54a-7e07-b6f5-bc28fa263615` |

The opaque identifier beside a specialist's delegation card is its delegation call ID. The child session IDs above come from the parent's native `subagent_session_id` results and match the children's task contexts. The [original capture index](../evidence/learner-demo/ui/index.json) preserves the unedited JPEG identities and capture timestamps.

## Earlier deployment foundation

The deployment recorded on 29 October uses kagent 0.10.3, Agent Substrate 0.0.9 and agentregistry 0.4.0 on the retained VM. Three sandbox agents reached Ready with golden snapshots and separate two-worker pools. Their versioned catalog entries identify the actual runtime image, Kubernetes resource UID and agent-specification hash. Current availability depends on an active VM session and fresh feature data.

This page retains the original platform foundation. Later checkpoints verify [GPU specialist/coordinator conversations](gpu_agent_routing.md), [MCP isolation](agent_network_isolation.md) and [automatic worker scaling with original UI captures](agent_scaling.md). A later [executed agent notebook](agent_notebook.md) verifies Feast-backed features, RAG and drift. A later [matched cold/golden comparison](agent_warmup.md) measured all three roles; its mixed timing results and single-host HA limits are explicit.

![Three sandbox agents using the same local model](../evidence/agent-platform/ui/native-ready-agents.jpg)

## How the components fit together

The retrieval specialist has three MCP tools: read machine features, obtain the model prediction and retrieve versioned reference passages. The drift specialist has one cohort-comparison tool. The coordinator has A2A references to the specialists. None has a pump-control or arbitrary-shell tool.

All three reference `default-model-config`, which selects the existing Qwen2.5-7B backend through llm-d. `OpenAI` in this configuration names the compatible API protocol. It does not select a paid external OpenAI model. The applied settings and the generated per-agent settings are checked independently, without exporting their credentials.

Substrate creates actors inside gVisor workers. A worker is a slot for an executing actor, rather than a permanently running copy of one chat session. The original foundation kept two workers per role. A later [KEDA experiment](agent_scaling.md) verified automatic 2 → 4 → 2 scaling for all three pools. Idle sessions can snapshot and release a slot. The pools share one control VM, so additional workers do not establish host-level high availability.

The installation follows the version-compatible [kagent Substrate guide](https://kagent.dev/docs/kagent/0.x/examples/agent-substrate/). The controller verifies the Substrate API certificate using the generated public CA. JWT authentication uses the retained cluster's ServiceAccount issuer. The trusted runtime workers and atelet require host privileges. These are distinct from the isolated agent actor and from the unprivileged MCP services.

![Actual Substrate inventory with three Ready templates and separate worker pools](../evidence/agent-platform/ui/native-substrate-workers.jpg)

![Applied model configuration with the internal backend and CPU response budget](../evidence/agent-platform/ui/native-global-model-config.jpg)

## Storage and private access

One retained 4-GiB PostgreSQL volume backs separate kagent and registry databases and users. Passwords are generated privately and referenced through Secrets. The catalog uses the external persistent database option described in the [registry installation documentation](https://aregistry.ai/docs/quickstart/), rather than its ephemeral development database.

Substrate keeps six 1-GiB Valkey volumes and a 16-GiB local object-store volume on the existing SSD. A guarded Ansible bind mount puts the runtime's canonical host directory on that SSD. The second application reported zero changes. No VM, disk, cloud bucket or Internet-facing port was added.

The controller, registry and UI remain private ClusterIP services. The screenshots use temporary loopback forwarding through IAP. These foundation screenshots precede the [authenticated agent gateway](agent_gateway.md), which records private HTTPS, identity forwarding, rate limiting and a checked coordinator conversation. Internal ClusterIP access and the later gateway are separate connection boundaries.

## Registry publication

Each catalog entry records the actual immutable Go ADK image, agent resource UID, specification hash and ActorTemplate name. Publication and versioned HTTP readback succeeded for retrieval, drift and coordinator. The catalog stores governance metadata; it does not deploy the Helm-managed SandboxAgent configuration.

![The three actual versioned agent entries in the private registry](../evidence/agent-platform/ui/native-registry-three-agents.jpg)

## What the integration checks found

The first deployment was rejected during server-side validation because RemoteMCPServer required a description. The next installation had running workers but could not select them. kagent's generated selector matches a label on the WorkerPool resource; adding that exact label and notifying its worker records resolved the mismatch. The original failed and interrupted runs are retained.

The native A2A client also needed wire version `0.3` and a message `contextId`. After that, the controller's network policy needed the router Pod's actual port 8080 in addition to the Service-facing port. These fixes produced real tool calls from a gVisor actor.

Two retained retrieval attempts contain six actual MCP tool results in total. Features and prediction share the same trace, served model version is 6, and returned knowledge passages match their original source hashes. One attempt failed during the final model response. A later task reached protocol completion but cut off a quotation at the 4,096-token context limit. Its reported prompt and generated token counts sum to exactly 4,096. Neither is accepted as a complete conversation.

The CPU response timeout was increased from 180 to 600 seconds. The output cap is 384 tokens. The same model's context was then increased to 8,192 tokens, confirmed through `/props` and Kubernetes arguments. Weights, image and resource limits stayed the same. The one-node model rollout used Recreate because a simultaneous second model reservation would not fit the scheduling budget. This causes a brief lab interruption.

Initial operator diagnostics did not establish the required isolation boundary. Their unsuccessful results remain visible. Ready gVisor snapshots alone are not substituted for an exercised allow/deny test. The later [startup benchmark](agent_warmup.md) records 24 fresh actors and retains the mixed results.

## Verify the recorded checkpoint

The historical [response baseline](agent_response_contract.md) adds a complete drift answer and preserves the earlier timeout. The later [GPU checkpoint](gpu_agent_routing.md) verifies complete policy 0.2.2 specialist/coordinator answers. Those subsequent results do not replace the original 0.1 receipts checked by this page's historical verifier.

```powershell
python -S scripts/verify-agent-platform.py
python -m unittest tests.test_agent_probe -q
```

The first command checks 528 source, command, readback and image payloads. It verifies applied configuration, native Ready states, catalog identities, tool-result provenance and the retained failed/partial outcomes. It explicitly reports complete chat, boundary probes, autoscaling, warm-up and overall coursework completion as unverified. The nine client tests check protocol/error handling with fixtures; they are not native model evidence. Captures retain the exact historical client, and the verifier separately reports whether it matches the current source.

[Agent learning guide](../README.md) · [Live demo steps](../edai-1/operations.md) · [LLM requirements](rubric_tracker.md) · [Original capture inventory](../evidence/agent-platform/capture.json)
