# Infrastructure and delivery

Configuration is grouped by deployed responsibility. Dockerfiles and dependency pins describe runtime images; Helm describes workloads; Terraform and Ansible describe the retained cloud/host layers. The command entry points under [scripts](../scripts/README.md) apply and verify those boundaries.

| Directory | Responsibility |
|---|---|
| [gcp](gcp/), [ansible](ansible/) | CPU/GPU resource configuration, host storage/runtime and worker roles |
| [charts](charts/), [agents](agents/) | Helm workloads, agent/worker configuration and release values |
| [ci](ci/) | Jenkinsfiles, immutable release contracts and test/build/deploy images |
| [feature-api](feature-api/), [predictor](predictor/), [drift-api](drift-api/), [mcp](mcp/) | API/service image and deployment assets |
| [llm](llm/), [llm-ab](llm-ab/), [rag](rag/) | Model serving, bounded comparisons and reference-pipeline pins |
| [gateway](gateway/), [security](security/), [mesh](mesh/), [policy](policy/) | Private routes, Vault consumers, network controls and admission rules |
| [observability](observability/), [llm-observability](llm-observability/) | Metrics/logs/traces, dashboard assets and observer pipelines |
| [spark](spark/), [flink](flink/), [airflow](airflow/), [datahub](datahub/) | Data-processing, scheduling and governance runtime assets |

See [recorded infrastructure execution](../docs/llm-infrastructure-evidence.md), [private gateway](../docs/agent-gateway.md) and [operations](../docs/operations.md). Resource templates do not establish deployment by themselves. Credentials, Terraform state, descriptors and bulk runtime outputs stay local.
