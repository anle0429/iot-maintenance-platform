# LLM and agent workstream

**Status: inference, bounded grounding, RAG governance and private MCP deployment/API checks verified; agent workstream incomplete.** A real llm-d router and CPU model have [native proof](llm-inference.md). The [RAG pipeline](rag-orchestration.md) and [three cited responses](llm-grounding.md) have separate acceptance records. [MCP tools](mcp-tools.md) now call those APIs through the native transport. The [agent foundation](agent-platform.md) now has native controller/catalog, Ready gVisor templates and initial tool-history proof. Complete conversations, boundary acceptance, autoscaling, warm-up and the mutation gate remain separate. Start with the [learning guide](llm-learning-guide.md) to follow the information flow.

![Staged LLM and agent components, with verified inference and existing ML endpoints distinguished](images/platform-llm-plan.png)

[Editable SVG](images/platform-llm-plan.svg) · [Source](diagrams/platform.py) · [LLM rubric](final-llm-rubric-tracker.md)

## Intended IoT interaction

A user asks about a machine's recent condition, risk or a relevant technical document. A coordinator delegates structured feature/RAG retrieval and drift analysis to two specialists. Authenticated MCP tools read the existing machine APIs and a separate versioned knowledge store. Responses identify source records and retrieved chunks. The design has no physical actuator tool.

## Required components

| Area | Required work | Existing baseline |
|---|---|---|
| Inference | llm-d platform, custom server, global model configuration, measured optimization | Router/custom backend and CPU-thread comparison verified; shared agent model config applied |
| Knowledge | Authored corpus, Airflow text/chunk/embedding pipeline, Feast storage, validation and DataHub lineage | [Six-task Airflow/GMS run, ten checks and invalid-input rejection](rag-orchestration.md); [source-cited generation](llm-grounding.md) verified on three questions |
| Tools | Async validated data/chunk and drift MCP services, health checks, Helm rollout/fallback | [Two native MCP releases](mcp-tools.md), each with two replicas and verified rollback |
| Agents | Retrieval and drift specialists, coordinator, registry publication, multi-replica autoscaling and sandbox | [Three Ready sandbox agents and published catalog identities](agent-platform.md); complete chat, boundary and scaling acceptance open |
| Startup | Agent Substrate warm-up, worker-pool HA explanation and startup benchmark | Native golden snapshots and two workers per role; measured cold/warm comparison and HA explanation open |
| Delivery | RAG and agent/MCP test-build-auto-deploy pipelines, gateway/UI access and secrets | ML CI/gateway/Vault are reusable baselines |
| Evaluation | Notebooks, coverage >90%, mutation >80%, boundary/property tests and native load HTML | [79 native MCP tests and load reports](mcp-tools.md); mutation and future agent checks remain open |
| Operations | Token counts, round-trip time, TTFT, safety/PII hits, agent/tool calls/failures and A/B | Existing metrics/log/trace backends; new telemetry missing |
| Extensions | Two documented LLM-track ideas with working proof | Selection and acceptance still open |

## Acceptance evidence

Every LLM row remains open until its own implementation and appropriate evidence exist. At minimum, preserve source/model/config identities; actual process/Pod readiness; native inference/tool/chat responses; RAG chunk/embedding/Feast readbacks and DataHub lineage; registry/sandbox/replica/warm-up observations; CI stage results; test/coverage/mutation/load reports; telemetry comparisons; and actual A/B behavior.

An architecture drawing, generated YAML, a mocked test or an unrelated ML dashboard is not native LLM proof. A proposed model is not described as deployed, and new cloud resources are not assumed authorized by this plan.

The next conversation iteration uses the [compact response contract](agent-response-contract.md). Its native predecessor contains one complete drift answer, an incomplete retrieval answer and no coordinator response. Policy 0.2 and its resource-guarded rollout are prepared locally; their deployment, latency and complete delegation remain unverified.

## Workbook references

The source links point to [llm-d](https://llm-d.ai/docs/getting-started/quickstart), [kagent custom providers](https://kagent.dev/docs/kagent/supported-providers/byo-openai), [agent registry](https://aregistry.ai/), [sandbox agents](https://kagent.dev/docs/kagent/examples/agent-sandbox), [Agent Substrate](https://kagent.dev/docs/kagent/concepts/agent-substrate), [KEDA](https://keda.sh/) and [Feast RAG](https://docs.feast.dev/tutorials/rag-with-docling). These are requirement references retained from the supplied workbook; no installation or compatibility is claimed from merely listing them.
