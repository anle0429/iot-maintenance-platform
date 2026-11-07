# Agentgateway, shared model configuration and A2A

Agentgateway is the selected private route for all three production agents. The 7 November run retained the cutover, persisted the two specialist MCP references in Helm, and completed Jenkins coordinator build **14** against that configuration. A separate post-deployment check exercised the A2A entry again. The Kubernetes resource still carries its original name, `iot-agentgateway-candidate`; that name no longer describes its activation status.

## Calls and ownership

```text
Retrieval / Drift / Coordinator
  -> shared default-model-config -> Agentgateway :18080
  -> llm-d / EPP -> observation API + model-control -> private GPU llama.cpp

Retrieval -> iot-gateway-retrieval-tools -> Agentgateway :18081 -> Retrieval MCP
Drift     -> iot-gateway-drift-tools     -> Agentgateway :18082 -> Drift MCP
Operator  -> authenticated A2A :18083 -> kagent controller -> Coordinator
Coordinator -> controller-routed delegation -> Retrieval and Drift specialists
```

The model route is used throughout each agent's reasoning and tool loop. It is not only a final-answer step. The specialists initiate their own MCP calls and return the results through their agent tasks. Inbound A2A and internal child delegation are different boundaries: **the parent enters through Agentgateway; child delegation remains controller-routed**.

| Listener | Actual client / operation | Fixed backend |
|---|---|---|
| 18080 | Shared OpenAI-compatible ModelConfig, `/v1/chat/completions` | Existing llm-d Service on port 8081 |
| 18081 | Retrieval MCP `/mcp` | Retrieval backend only; three allowed tools |
| 18082 | Drift MCP `/mcp` | Drift backend only; one allowed tool |
| 18083 | Three exact A2A task paths and agent cards | Existing kagent controller; fixed authenticated operator identity |

The canonical A2A paths and their trailing-slash aliases are both explicit routes. Unknown controller paths and missing or cross-role credentials are rejected. Model and MCP credentials are mounted from their existing Secrets; no credential value is committed. NGINX retains the private UI and Feature API front doors.

<a id="declared-settings"></a>

## Configuration in code

[Gateway configuration](../../infra/agentgateway/candidate.py) pins Agentgateway v1.7.0. [The agent overlay](../../infra/agentgateway/agents-default.yaml) selects the stable gateway MCP resources in [the chart](../../infra/charts/iot-agents/templates/tools.yaml). [The kagent overlay](../../infra/agentgateway/kagent-default.yaml) selects the model listener. The chart's direct mode remains available for the original bootstrap and controlled comparisons; CI explicitly requires gateway mode.

```yaml
# infra/agentgateway/kagent-default.yaml
providers:
  openAI:
    config:
      baseUrl: http://iot-agentgateway-candidate.iot-agents.svc.cluster.local:18080/v1
```

| Shared setting | Accepted value |
|---|---|
| ModelConfig | `default-model-config`, referenced by all three roles |
| Model alias | `iot-qwen2.5-7b` |
| API format | `chatCompletions` |
| Output budget | 768 tokens; timeout 600 seconds |
| Sampling | Temperature 0, seed 7 |
| Client Secret | `iot-local-model-client`, key `API_KEY` |

These are the LLM settings, separate from the failure predictor's MLflow model version 6. [CI contract validation](../../agents/ci_contract.py) rejects mixed direct/gateway tool references. [OPA](../../infra/policy/agent-admission.rego) admits one complete reviewed configuration selected by the actual ModelConfig endpoint; it does not combine independent allowances from both modes.

<a id="native-results"></a>

## Native agentgateway integration

| Check | Recorded result |
|---|---|
| Model route | Nine genuine generations joined to the actual worker Pod IPs across retrieval, drift and coordinator |
| MCP | Both listeners executed their expected tools; four business calls in the coordinator conversation |
| A2A | Parent accepted through Agentgateway; both independently fetched child histories passed |
| Retention | `--keep` accepted; shared model route remained selected |
| Helm persistence | Stable `iot-gateway-retrieval-tools` and `iot-gateway-drift-tools` references applied and Ready |
| CI | Coordinator build 14 SUCCESS with pinned MLflow prompts and OPA; shared ModelConfig unchanged afterward |

[Accepted routed conversation](../evidence/mentor-runtime-20261107/gateway-conversation.json) · [Helm result](../evidence/mentor-runtime-20261107/gateway-default.json) · [Applied model](../evidence/mentor-runtime-20261107/applied-model.json) · [Agent specifications](../evidence/mentor-runtime-20261107/agents-default.json) · [Build 14](../evidence/mentor-runtime-20261107/ci-verify.json)

The observer's synchronous metric reconstruction had blocked its ASGI event loop under repeated EPP scrapes. A native stack capture identified that path. The fix shares a bounded five-second metric snapshot and performs reconstruction in a worker thread. Twenty-five consecutive observations retained the same Pod with zero restarts and successful health requests. This addresses the observed no-eligible-endpoint 503; it is not a claim of indefinite uptime. The fresh observer gate passed 98 tests, statement coverage 99.81%, branch coverage 98.47% and **2,387 / 2,974 killed mutants (80.26%)**, with all survivors and exits retained. [Quality result](../evidence/mentor-runtime-20261107/observer-quality.json) · [Implementation](../../llm_observability/metrics.py).

The complete native archive includes earlier 503s, the rejected stale gateway configuration, and recorder failures. Those records were not relabelled as successful runs. Build 13 failed before admission because the new sealed source directories were unreadable to Jenkins; build 14 used the same source bytes after scoped directory-permission repair. The earlier [November cutover](../evidence/agentgateway-native/summary.json) remains a separate temporary run whose original client configuration was restored.

<a id="repeat-the-controlled-check"></a>

## Reproduce the selected route

1. Recover the retained CPU/GPU services within their approved stop windows. Verify controller, MCP backends, GPU model and observer readiness, then prepare a genuine unexpired feature lease. Keep all credentials on the VM.
2. Upload a sealed source packet. Run [the smoke driver](../../scripts/native-gateway-driver.py) against the actual official binary and private backends. It validates the current ConfigMap before resuming an existing candidate.
3. Run [the cutover driver](../../scripts/native-gateway-cutover.py) with `--keep` only after its native acceptance checks pass. Without that flag, the experiment restores its original client configuration.
4. Persist the selected mode in both Helm releases, preserving the current private values and pinned kagent chart:

   ```bash
   helm upgrade kagent "$PINNED_KAGENT_CHART" -n iot-agents \
     -f "$PRIVATE_KAGENT_VALUES" -f infra/agentgateway/kagent-default.yaml \
     -f infra/llm-observability/kagent-tracing.yaml --atomic --wait --timeout 240s
   helm upgrade iot-agents infra/charts/iot-agents -n iot-agents \
     -f "$PRIVATE_AGENT_VALUES" -f infra/agentgateway/agents-default.yaml \
     --post-renderer scripts/render-agent-responses.py --atomic --wait --timeout 240s
   ```

5. Submit the source-bound Jenkins job with three exact MLflow prompt versions. Verify the applied ModelConfig and specialist URLs again after deployment; HTTP 200 alone does not establish A2A task success.
6. Check the recorded results offline with `python -B -S scripts/verify-mentor-runtime.py`.

<a id="mcp-tools-request-handling-and-helm-recovery"></a>

The [MCP chapter](mcp_tools.md) contains the asynchronous handlers, RollingUpdate, automatic rollback and MCP autoscale proof. [Observability](observability.md) contains automatic task status and complete Jaeger graphs. The approved reference lab has one CPU control node and one private GPU worker; this run does not establish multi-node HA or public production availability.

<a id="attempt-to-retain-the-default-route"></a>

## Earlier retention failure

The earlier keep attempt restored the direct route after the model router reported no eligible endpoint. It remains a failed predecessor, distinct from the accepted retained route, Helm persistence and build 14 described above. The original receipts are retained in the native archives; the successful follow-up does not rewrite that attempt.
