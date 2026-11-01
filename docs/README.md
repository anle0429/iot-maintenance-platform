# Technical documentation

The platform has three connected layers: pump data, model development/serving, and tool-grounded agents. Start with one request and follow its source records, feature values, model pin and tool results. The diagrams describe the recorded lab; they are separate from runtime screenshots.

The [architecture gallery](diagrams/README.md) contains ten coordinated Excalidraw views with static and animated exports: system map, data/ML overview, prediction, deployment, observability, RAG/agents, release admission, code boundaries, trace paths and prompt policy.

## Review path

For the data-platform review, [input characteristics and before/after evidence](data-processing-review.md) brings together generator measurements, native Spark/Flink views, an executed schema notebook and stage-specific Airflow/DataHub coverage. [Container image optimization](docker-optimization.md) records an 85.5% full/slim size reduction with identical HTTP predictions. [Controlled batch experiments](batch-corrections-benchmark.md) add three paired measurements per comparison and an actual five-task Airflow run, including the slower dedup candidate and unchanged production choice.

| Review question | Start here | Implementation |
|---|---|---|
| What problem does the system solve? | [Business domain](../README.md#business-domain), [data contract](data-contract.md) | [Generator](../generator/README.md) |
| How does data become features? | [Architecture](architecture.md), [batch/stream entry points](../pipelines/README.md), [Airflow and DataHub](orchestration.md) | [Pipelines](../pipelines/), [DAGs](../airflow/dags/) |
| What actually ran at 100 GB? | [Full-scale ingest](full-scale-ingest.md), [resource protocol](scale-100gb.md) | [Chunk ingestion](../pipelines/chunk_source.py) |
| Which batch changes improved the measured workload? | [Schema/dedup comparisons](batch-corrections-benchmark.md), [Airflow skew execution](skew-benchmark.md#airflow-benchmark-execution) | [Benchmark job](../pipelines/data_corrections_benchmark.py), [DAG](../airflow/dags/iot_data_benchmarks.py) |
| How are labels, splits and model versions controlled? | [Evaluation protocol](ml-evaluation-protocol.md), [five ML classes](ml-class-design.md), [notebook](training-notebook.md) | [ML library](../ml/), [feature store](../feature_store/) |
| What serves a prediction? | [Feature API](feature-api.md), [model serving](model-serving.md), [serving CI](ci-serving.md) | [Services](../services/README.md) |
| How does the assistant use data and references? | [LLM platform](llm-platform.md), [RAG](rag-orchestration.md), [MCP](mcp-tools.md), [agent routing](gpu-agent-routing.md) | [Agents](../agents/), [RAG](../rag/), [knowledge](../knowledge/) |
| How was a complete answer checked? | [Post-restart agent validation](agent-runtime-validation.md), [testing techniques](mcp-quality-evidence.md) | [Answer acceptance](../agents/answer_acceptance.py), [delegation acceptance](../agents/delegation_acceptance.py) |
| How are releases and infrastructure operated? | [CI/CD](ci-agents.md), [infrastructure](llm-infrastructure-evidence.md), [operations](operations.md) | [Infrastructure](../infra/), [command index](../scripts/README.md) |
| Where are measurements and security proofs? | [Monitoring](llm-monitoring-demo.md), [private gateway](agent-gateway.md), [Vault](vault-agent-ui.md), [model A/B](llm-model-ab.md), [agent A/B](agent-configuration-ab.md) | [Observability](../llm_observability/), [security](../infra/security/) |
| Which evidence supports each requirement? | [117-row map](final-audit-matrix.md), [ML tracker](final-rubric-tracker.md), [LLM tracker](final-llm-rubric-tracker.md), [audit](final-audit.md) | [Evidence format and archive](evidence-guide.md) |

## Reading a component page

A component page identifies its input/output boundary, code, runtime location and recorded validation. Relative code links point to the maintained checkout. Commit-pinned links and native receipts refer to historical executions with their original versions. Failed outcomes, wrong answers and measurement limits are retained.

Run examples from the repository root. The [operations runbook](operations.md) describes the retained private lab; a clone contains no runtime credentials, bulk data, service state or LLM weights. For standalone offline verifiers, first run `python -B -S scripts/unpack-evidence.py`. The full audit does this automatically.

[Source history migration](history-rewrite.md) explains changed commit identities and verification of original source bindings.
