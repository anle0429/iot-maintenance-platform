# Mentor review follow-up

Start with the two current platform views. Data Platform v16 puts DP1–DP3 in the Spark execution path. ML + LLM v19 shows the inputs to offline drift and the separate metrics for RAG Search and Drift MCP. [Current diagrams](diagrams/README.md#current-platform-diagrams)

## CPU lab checks on November 24, 2026

| Check | Recorded result |
|---|---|
| Drift reference matches the model being served | Drift MCP accepted 32 operating pumps for model v6, using 1,024 rows from that model's training partition. |
| Search can scale independently | RAG Search scaled from two to four ready Pods and back to two. Retrieval MCP stayed at two replicas. |
| Prometheus collects capacity and evaluates alerts | The retained server scraped Drift API and loaded both capacity rules. A separate Prometheus process fired and cleared the rules using controlled test inputs. |

[CPU lab results](architecture/ml-llm-platform-v18-notes.md#cpu-lab-verification-on-november-24-2026) · [Current drift and scaling design](architecture/ml-llm-platform-v19-notes.md)

## Earlier review topics

The November 7–8 gateway and policy checks cover coordinator delegation, specialist tool calls, policy acceptance and Jenkins releases. The chapter links below identify the implementation and the recorded run for each topic.

| Review | Primary chapter | Verified result and boundary |
|---|---|---|
| Architecture must show application flows, callers, APIs and pipelines | [Architecture and execution guide](diagrams/execution-guide.md) | The current system views and seven earlier execution views cover requests, job inputs, stores and release gates. |
| Redis and Feast responsibilities | [Data storage](edai-1/data_storage.md) | Offline Parquet/FileSource and embedded Feast callers are separate from release-specific Redis. Eight prediction inputs remain separate from four finalized stream monitoring aggregates. |
| Medallion modeling: tables and links in each zone | [Data storage — zone model](edai-1/data_storage.md#schema-design) | Editable thirteen-table original-batch lineage, exact grains/counts and native Gold key checks. Silver cleanup, event-grain Gold facts and ML aggregates are distinct; the fourteen-table staged Airflow layout and 100 GB Silver boundary are documented separately. |
| Window code; one batch/stream chapter | [Processing jobs](edai-1/processing_jobs.md) | Actual Flink timestamp/watermark/window code, late data, replay, Spark corrections and Airflow proof are in the same chapter. |
| Lakehouse/warehouse code and optimization | [Data storage](edai-1/data_storage.md) | Row-checked Parquet compaction; separate Gold DuckDB index comparison, median 143.994 → 45.275 ms, with 2,883,584 additional bytes. Deployed storage remains Parquet. |
| Inference setup, real model call and requested harness | [LLM inference platform](edai-2/llm_inference_platform.md) | Step-by-step setup, actual 7B calls and official llm-d-benchmark. Two 30-second trials completed 6/11 requests, with 2/5 incomplete and zero errored. Original converter/startup failures remain identified. |
| Shared ModelConfig through Agentgateway; default route 503 | [Agentgateway and ModelConfig](edai-2/global_model_config.md) | All three roles used the gateway. Observer event-loop starvation fixed and source-tested. The route was retained in Helm and preserved through build 18; both child tasks were checked again against native gateway logs after deployment. |
| Async MCP, Helm RollingUpdate and automatic fallback | [MCP tools](edai-2/mcp_tools.md) | Async handlers and bounded I/O, actual Helm atomic rollback for both services, SDK checks after recovery. |
| Agentgateway for MCP and A2A | [Agentgateway and ModelConfig](edai-2/global_model_config.md) | Selected MCP aliases and both Coordinator-to-specialist task paths traverse Agentgateway. Native logs join each child to its parent/root context and Coordinator Pod IP/UID. The controller remains the session/actor backend. |
| Retained A/B agents must work with the selected gateway | [Agent configuration comparison](edai-2/agent_configuration_ab.md#gateway-compatibility-replay) | Four retained resources migrated without changing sampling settings or production specs. Six new tasks: 18 MCP calls, 24 model requests, zero tool errors and independent model 6 score error 0. Original direct-profile results remain separate. |
| Specialist chat showing actual MCP execution | [Agent platform](edai-2/agent_platform.md#native-specialist-tools) | New parent and two child sessions; three retrieval tools and one drift tool. Original expanded argument/result JPEGs and complete raw histories are retained. |
| Invalid policy configuration must be blocked | [Agent admission policy](edai-2/agent_admission_policy.md) | 79 native OPA cases: 4 allow, 75 deny. Jenkins 19 blocked `start_pump`; 27 resource UID/specs, three Helm revisions and the GPU output-token counter stayed unchanged. |
| Substrate benefit/control comparison | [Agent warm-up](edai-2/agent_warmup.md) | Ten accepted requests, four alternating measured pairs. Substrate median 6.497 s versus ordinary Agent 4.234 s: 53.46% slower. No general latency or cost improvement claimed. Reusable capacity is measured separately. |
| Prompt management in CI/CD | [Agent CI/CD](edai-2/ci_agents.md) | MLflow Prompt Registry version/hash admission. Build 18 loaded all three v1 prompts, passed OPA and deployed/verified a complete coordinator conversation; build 19 was denied before deployment. |
| Jaeger incomplete graphs, unknown service and outcomes | [Observability](edai-2/observability.md) | Automatic caller ERROR/OK for real failed/completed tasks; original 10-/44-span graphs have zero missing parents and five/seven named services. SDK UNSET statuses and historical inventory remain untouched. |
| Actual autoscale and per-Pod proof | [MCP tools](edai-2/mcp_tools.md#native-mcp-autoscale) / [Worker scaling](edai-2/agent_scaling.md#latest-native-worker-run) | In the earlier setup, both MCP Deployments and all three worker pools completed 2 → 4 → 2. The MCP run logged 7,746 successful load requests. The worker run verified 11 coordinator conversations and observed active actors in all 12 Pods. |

## Evidence and reproduction

[8 November review capsule](evidence/agent-delegation-20261108/manifest.json) · [Policy, chat and CI checks](evidence/agent-delegation-20261108/verification.json) · [Original native archive](evidence/agent-delegation-20261108/native-receipts.tar.gz).

[A/B gateway replay](evidence/agent-ab-gateway-20261108/verification.json) · [Original A/B gateway archive](evidence/agent-ab-gateway-20261108/native-receipts.tar.gz).

[Native follow-up capsule](evidence/mentor-runtime-20261107/manifest.json) · [Jenkins result](evidence/mentor-runtime-20261107/ci-verify.json) · [Original runtime archive](evidence/mentor-runtime-20261107/native-receipts.tar.gz) · [Original CI archive](evidence/mentor-runtime-20261107/ci-receipts.tar.gz).

```bash
python -B -S scripts/unpack-evidence.py
python -B -S scripts/verify-mentor-runtime.py
python -B -S scripts/verify-agent-delegation-review.py
python -B -S scripts/verify-agent-ab-gateway.py
```

The verifier authenticates every archived byte, recomputes mutation exits, graph parent closure and terminal states, verifies actual child histories, reconstructs scaling observations and recalculates matched medians. The original failed attempts remain available. Exact source evolution is authenticated before older checkers compare their original checkpoint; earlier evidence is never rewritten as if it ran the new implementation.

The architecture views show application responsibilities and the private lab layout. Each experiment records its own input, configuration and time window. The 100 GB ingest ends at Silver. The model uses the separate 60-day workload. [Storage and results](edai-1/processing_jobs.md#workload-boundaries)
