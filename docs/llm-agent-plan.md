# LLM and agent workstream

**Status: initial LLM inference verified; remaining agent workstream incomplete.** A real llm-d router and CPU model have [native proof](llm-inference.md). Existing ML APIs provide integration points; they do not establish MCP or agent execution. Start with the [learning guide](llm-learning-guide.md) to follow the information flow.

![Staged LLM and agent components, with verified inference and existing ML endpoints distinguished](images/platform-llm-plan.png)

[Editable SVG](images/platform-llm-plan.svg) · [Source](diagrams/platform.py) · [LLM rubric](final-llm-rubric-tracker.md)

## Intended IoT interaction

A user asks about a machine's recent condition, risk or a relevant technical document. A coordinator delegates structured feature/RAG retrieval and drift analysis to two specialists. Authenticated MCP tools read the existing machine APIs and a separate versioned knowledge store. Responses identify source records and retrieved chunks. The design has no physical actuator tool.

## Required components

| Area | Required work | Existing baseline |
|---|---|---|
| Inference | llm-d platform, custom server, global model configuration, measured optimization | Router/custom backend and CPU-thread comparison verified; agent-global configuration pending |
| Knowledge | Authored corpus, Airflow text/chunk/embedding pipeline, Feast storage, validation and DataHub lineage | [Native five-stage core verified](rag-foundation.md); Airflow/DataHub integration pending |
| Tools | Async validated data/chunk and drift MCP services, health checks, Helm rollout/fallback | Feature/drift HTTP APIs are not yet MCP deployments |
| Agents | Retrieval and drift specialists, coordinator, registry publication, multi-replica autoscaling and sandbox | None deployed |
| Startup | Agent Substrate warm-up, worker-pool HA explanation and startup benchmark | Not implemented |
| Delivery | RAG and agent/MCP test-build-auto-deploy pipelines, gateway/UI access and secrets | ML CI/gateway/Vault are reusable baselines |
| Evaluation | Notebooks, coverage >90%, mutation >80%, boundary/property tests and native load HTML | ML test results do not cover new MCP/agent code |
| Operations | Token counts, round-trip time, TTFT, safety/PII hits, agent/tool calls/failures and A/B | Existing metrics/log/trace backends; new telemetry missing |
| Extensions | Two documented LLM-track ideas with working proof | Selection and acceptance still open |

## Acceptance evidence

Every LLM row remains open until its own implementation and appropriate evidence exist. At minimum, preserve source/model/config identities; actual process/Pod readiness; native inference/tool/chat responses; RAG chunk/embedding/Feast readbacks and DataHub lineage; registry/sandbox/replica/warm-up observations; CI stage results; test/coverage/mutation/load reports; telemetry comparisons; and actual A/B behavior.

An architecture drawing, generated YAML, a mocked test or an unrelated ML dashboard is not native LLM proof. A proposed model is not described as deployed, and new cloud resources are not assumed authorized by this plan.

## Workbook references

The source links point to [llm-d](https://llm-d.ai/docs/getting-started/quickstart), [kagent custom providers](https://kagent.dev/docs/kagent/supported-providers/byo-openai), [agent registry](https://aregistry.ai/), [sandbox agents](https://kagent.dev/docs/kagent/examples/agent-sandbox), [Agent Substrate](https://kagent.dev/docs/kagent/concepts/agent-substrate), [KEDA](https://keda.sh/) and [Feast RAG](https://docs.feast.dev/tutorials/rag-with-docling). These are requirement references retained from the supplied workbook; no installation or compatibility is claimed from merely listing them.
