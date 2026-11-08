# Agentgateway, shared model configuration and A2A

Agentgateway is the selected private route for all three production agents. The **8 November** native follow-up also verified the coordinator's actual retrieval and drift child A2A requests through this gateway. The controller remains the session and actor-routing backend. The earlier 7 November retained model/MCP route and parent-only A2A transport proof are recorded separately below. The Kubernetes resource still carries its original name, `iot-agentgateway-candidate`; that name no longer describes its activation status.

## Calls and ownership

```text
Retrieval / Drift / Coordinator
  -> shared default-model-config -> Agentgateway :18080
  -> llm-d / EPP -> observation API + model-control -> private GPU llama.cpp

Retrieval -> iot-gateway-retrieval-tools -> Agentgateway :18083/mcp/retrieval -> Retrieval MCP
Drift     -> iot-gateway-drift-tools     -> Agentgateway :18083/mcp/drift -> Drift MCP
Operator  -> authenticated A2A :18083 -> kagent controller -> Coordinator
Private UI -> authenticated NGINX -> kagent UI / controller -> Coordinator
Coordinator -> Agentgateway :18083 -> kagent controller -> Retrieval and Drift child sessions
```

The model route is used throughout each agent's reasoning and tool loop. It is not only a final-answer step. The specialists initiate their own MCP calls and return the results through their agent tasks. Parent ingress, child A2A transport and controller session ownership have separate checks. In the fresh UI conversation, the parent used the private NGINX/UI entry and both internal child `message/send` requests traversed Agentgateway before reaching the controller.

| Listener | Actual client / operation | Fixed backend |
|---|---|---|
| 18080 | Shared OpenAI-compatible ModelConfig, `/v1/chat/completions` | Existing llm-d Service on port 8081 |
| 18081 | Retained dedicated retrieval MCP `/mcp` listener | Retrieval backend only; three allowed tools |
| 18082 | Retained dedicated drift MCP `/mcp` listener | Drift backend only; one allowed tool |
| 18083 | Selected MCP aliases `/mcp/retrieval` and `/mcp/drift` | The same role-specific MCP backends and credentials |
| 18083 | Three operator A2A paths/cards; two specialist delegation paths/cards | Existing kagent controller; separate operator and delegation credentials |
| 18083 | Two explicitly enabled A/B operator paths/cards | The same controller and operator credential; no additional delegation capability |

The canonical A2A paths and their trailing-slash aliases are both explicit routes. Operator ingress fixes the authenticated lab identity to `coursework`. The delegated specialist routes use a distinct scoped credential, require the compiler's exact `x-kagent-host` marker, and preserve the SDK's user identity, parent/root context and trace headers. Gateway authentication and routing headers are removed before forwarding to the fixed controller backend. Unknown controller paths and missing or cross-role credentials are rejected. Model and MCP credentials remain in their existing Secrets. NGINX retains the private UI and Feature API front doors.

<a id="declared-settings"></a>

## Configuration in code

[Gateway configuration](../../infra/agentgateway/candidate.py) pins Agentgateway v1.7.0. [The agent overlay](../../infra/agentgateway/agents-default.yaml) selects the stable gateway MCP resources in [the chart](../../infra/charts/iot-agents/templates/tools.yaml). [The kagent overlay](../../infra/agentgateway/kagent-default.yaml) selects the model listener and both child-A2A routing settings. The chart's direct mode belongs to the original bootstrap/comparison profile; CI explicitly requires gateway mode.

**A/B experiment profile.** The retained `iot-ab-retrieval-a/b` resources have now been migrated in place. Both distinct ModelConfigs use listener 18080; both agents use `iot-gateway-retrieval-tools`, alias `/mcp/retrieval` on 18083, and the gateway domain. Two exact authenticated operator routes on 18083 make their advertised A2A interfaces usable. The new routes require the explicit `ab_operator_routes=True` option; the renderer's default three-role profile remains unchanged. Their four original resource UIDs, temperatures 0/0.3, seed, token budget, prompt and retrieval worker pool were retained. Six fresh tasks produced 18 verified MCP tool calls and 24 actor-attributed model requests. [The A/B chapter](agent_configuration_ab.md#gateway-compatibility-replay) contains the migration commands, complete results and original failures. This controlled experiment uses separate ModelConfigs; it does not change the shared production configuration or automatically promote either variant.

```yaml
# infra/agentgateway/kagent-default.yaml
controller:
  a2aBaseUrl: http://iot-agentgateway-candidate.iot-agents.svc.cluster.local:18083
proxy:
  url: http://iot-agentgateway-candidate.iot-agents.svc.cluster.local:18083
providers:
  openAI:
    config:
      baseUrl: http://iot-agentgateway-candidate.iot-agents.svc.cluster.local:18080/v1
```

Both A2A settings matter in kagent 0.10.3. `proxy.url` mediates the compiler's initial child-card discovery; `controller.a2aBaseUrl` keeps the advertised JSON-RPC interface on the gateway. The compiler preserves the request path but drops its original port, so the selected MCP resources use the two explicit role aliases on port 18083. [Coordinator tool declarations](../../infra/charts/iot-agents/templates/agents.yaml) attach the dedicated delegation Secret without exposing its value. Direct controller access remains available for runtime task/history callbacks; this is not a universal direct-A2A bypass-blocking claim.

| Shared setting | Accepted value |
|---|---|
| ModelConfig | `default-model-config`, referenced by all three roles |
| Model alias | `iot-qwen2.5-7b` |
| API format | `chatCompletions` |
| Output budget | 768 tokens; timeout 600 seconds |
| Sampling | Temperature 0, seed 7 |
| Client Secret | `iot-local-model-client`, key `API_KEY` |

These are the LLM settings, separate from the failure predictor's MLflow model version 6. [CI contract validation](../../agents/ci_contract.py) rejects mixed direct/gateway tool references. [OPA](../../infra/policy/agent-admission.rego) admits one complete reviewed configuration selected by the actual ModelConfig endpoint; it does not combine independent allowances from both modes.

## Native results

The fresh accepted parent context is `ed943603-418d-4269-9b0c-0d177a878244`. Its actual retrieval child is `01a1ba15-99e7-748b-8624-34298460e267`; its drift child is `01a1ba15-bbdf-7013-9fb0-3af972877a14`. The parent entered through authenticated private NGINX/UI HTTPS. Native gateway logs contain **two successful delegated `message/send` requests** on the specialist trailing-slash routes, each returning a completed task.

[Transport acceptance](../../agents/gateway_acceptance.py) binds the two gateway records to these independently selected child contexts, the same parent/root context and the actual coordinator worker Pod IP and UID. [Content acceptance](../../agents/delegation_acceptance.py) separately verifies both child histories, three retrieval MCP results, one drift MCP result and the unchanged combined answer. HTTP 200, agent-card discovery and a Completed wrapper alone cannot substitute for those checks.

[Native verification](../evidence/agent-delegation-20261108/verification.json) · [Review index](../evidence/agent-delegation-20261108/review-index.json) · [Archive receipt](../evidence/agent-delegation-20261108/native-archive.json) · [Original native archive](../evidence/agent-delegation-20261108/native-receipts.tar.gz) · [Expanded specialist chat captures](agent_platform.md#native-specialist-tools).

## Native Agentgateway integration

The 7 November run retained the model/MCP cutover, persisted the two specialist MCP references in Helm, and completed Jenkins coordinator build **14** against that configuration. A separate post-deployment check exercised parent A2A ingress again. Its child histories establish content acceptance; that earlier transport checkpoint covered parent ingress while internal child delegation remained controller-routed.

| Check | Recorded result |
|---|---|
| Model route | Nine genuine generations joined to the actual worker Pod IPs across retrieval, drift and coordinator |
| MCP | Both listeners executed their expected tools; four business calls in the coordinator conversation |
| A2A | Parent accepted through Agentgateway; both independently fetched child histories passed; internal child transport remained controller-routed |
| Retention | `--keep` accepted; shared model route remained selected |
| Helm persistence | Stable `iot-gateway-retrieval-tools` and `iot-gateway-drift-tools` references applied and Ready |
| CI | Coordinator build 14 SUCCESS with pinned MLflow prompts and OPA; shared ModelConfig unchanged afterward |

[Accepted routed conversation](../evidence/mentor-runtime-20261107/gateway-conversation.json) · [Helm result](../evidence/mentor-runtime-20261107/gateway-default.json) · [Applied model](../evidence/mentor-runtime-20261107/applied-model.json) · [Agent specifications](../evidence/mentor-runtime-20261107/agents-default.json) · [Build 14](../evidence/mentor-runtime-20261107/ci-verify.json)

The observer's synchronous metric reconstruction had blocked its ASGI event loop under repeated EPP scrapes. A native stack capture identified that path. The fix shares a bounded five-second metric snapshot and performs reconstruction in a worker thread. Twenty-five consecutive observations retained the same Pod with zero restarts and successful health requests. This addresses the observed no-eligible-endpoint 503; it is not a claim of indefinite uptime. The fresh observer gate passed 98 tests, statement coverage 99.81%, branch coverage 98.47% and **2,387 / 2,974 killed mutants (80.26%)**, with all survivors and exits retained. [Quality result](../evidence/mentor-runtime-20261107/observer-quality.json) · [Implementation](../../llm_observability/metrics.py).

The complete native archive includes earlier 503s, the rejected stale gateway configuration, and recorder failures. Those records were not relabelled as successful runs. Build 13 failed before admission because the new sealed source directories were unreadable to Jenkins; build 14 used the same source bytes after scoped directory-permission repair. The earlier [November cutover](../evidence/agentgateway-native/summary.json) remains a separate temporary run whose original client configuration was restored.

<a id="repeat-the-controlled-check"></a>

## Reproduce the selected route

1. Recover the retained CPU/GPU services within their approved stop windows. Verify controller, MCP backends, GPU model and observer readiness, then prepare a genuine unexpired feature lease. Keep all credentials on the VM.
2. Upload a sealed source packet. Collect the actual gateway objects, namespace network policies and controller Deployment. [The preparation tool](../../scripts/prepare-agentgateway.py), with `--delegation-key-hash` and `--delegation-readbacks`, uses [the guarded planner](../../infra/agentgateway/delegation.py) to bind changes to their current UID, resourceVersion and complete original field. Keep the delegation credential itself on the VM.
3. Evaluate the complete current agent configuration with OPA before deployment. Provision the dedicated delegation Secret before SDK initialization, then apply the reviewed gateway and narrow client-network changes. Preserve the existing model/MCP routes and controller callbacks. The earlier [smoke](../../scripts/native-gateway-driver.py) and [cutover](../../scripts/native-gateway-cutover.py) drivers reproduce the older parent-only experiment; they do not install this child-delegation configuration.
4. Persist the selected mode in both Helm releases, preserving the current private values and pinned kagent chart. Apply the agent chart's MCP aliases before changing the compiler proxy:

   ```bash
   helm upgrade iot-agents infra/charts/iot-agents -n iot-agents \
     -f "$PRIVATE_AGENT_VALUES" -f infra/agentgateway/agents-default.yaml \
     --post-renderer scripts/render-agent-responses.py --atomic --wait --timeout 240s
   helm upgrade kagent "$PINNED_KAGENT_CHART" -n iot-agents \
     -f "$PRIVATE_KAGENT_VALUES" -f infra/agentgateway/kagent-default.yaml \
     -f infra/llm-observability/kagent-tracing.yaml --atomic --wait --timeout 240s
   ```

5. Send a fresh coordinator question and retain the original parent, child sessions/tasks, gateway logs and worker Pod inventory. Require both transport and content acceptance. Inspect the same contexts in the private UI and preserve original captures. A source-bound Jenkins recheck uses three exact MLflow prompt versions; retain its actual outcome separately from conversation acceptance.
6. Inspect the 8 November verification and original archive linked above. The earlier 7 November capsule remains independently checkable with `python -B -S scripts/verify-mentor-runtime.py`.

<a id="mcp-tools-request-handling-and-helm-recovery"></a>

The [MCP chapter](mcp_tools.md) contains the asynchronous handlers, RollingUpdate, automatic rollback and MCP autoscale proof. [Observability](observability.md) contains automatic task status and complete Jaeger graphs. The approved reference lab has one CPU control node and one private GPU worker; this run does not establish multi-node HA or public production availability.

<a id="attempt-to-retain-the-default-route"></a>

## Earlier retention failure

The earlier keep attempt restored the direct route after the model router reported no eligible endpoint. It remains a failed predecessor, distinct from the accepted retained route, Helm persistence and build 14 described above. The original receipts are retained in the native archives; the successful follow-up does not rewrite that attempt.
