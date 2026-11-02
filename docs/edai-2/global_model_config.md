# Shared model configuration

Retrieval, drift and coordinator reference the same `default-model-config`. It selects a model through the private llm-d route; model weights, MCP execution and role-specific tool permissions remain separate. `OpenAI` names the compatible API protocol here, rather than an external paid model.

## Declared settings

[The kagent values](../../infra/agents/kagent-values.yaml) declare the provider. [The agent chart](../../infra/charts/iot-agents/templates/agents.yaml) references `.Values.modelConfig` for all three roles; [its values](../../infra/charts/iot-agents/values.yaml) select `default-model-config`.

| Setting | Source value |
|---|---|
| Model alias | `iot-qwen2.5-7b` |
| Endpoint | `http://iot-router-epp.iot-llm.svc.cluster.local:8081/v1` |
| API format | `chatCompletions` |
| Maximum output tokens | 384 |
| Temperature / seed | 0 / 7 |
| Request timeout | 600 seconds |
| Authentication reference | Secret `iot-local-model-client`, key `API_KEY`; its value remains outside Git |

These are declared settings, not current endpoint readiness. Role prompts and tools belong to the individual SandboxAgent specifications. Every role uses model generations; coordinator delegation and specialist MCP calls are distinct operations.

## Recorded deployment and refinements

![Original shared ModelConfig view from the platform foundation](../evidence/agent-platform/ui/native-global-model-config.jpg)

This foundation capture predates the later GPU/tool-budget refinements. Its visible settings belong to that deployment. [The original inference platform](llm_inference_platform.md), [shared GPU route](gpu_agent_routing.md) and [platform foundation](agent_platform.md) preserve their separate identities.

The later model-control adapter bounds retrieval to three MCP calls and drift to one, with an absolute eight-call history limit. This is separate from `maxTokens`; MCP executes the tools. Answer acceptance checks actual paired tool/child histories. [The coordinator trace](observability.md#trace-an-actual-coordinator-request) records nine model generations and four MCP calls.

[Model A/B](llm_model_ab.md) compares pinned weight artifacts. [Agent A/B](agent_configuration_ab.md) changes agent configuration under its fixed model/tool boundary. Neither experiment changes every production agent. Live operation requires the approved VM sessions, pinned weights, ready private route and fresh feature lease described in [operations](../edai-1/operations.md); the linked offline verifiers authenticate recorded evidence without making a model request.
