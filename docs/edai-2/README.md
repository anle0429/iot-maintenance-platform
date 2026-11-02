# LLM and agent platform

[Documentation](../README.md) · [Other part](../edai-1/README.md)

Follow a machine question through shared inference, specialist tools, retrieved references and coordinator delegation. Each page separates actual model generations, MCP calls and accepted answers.

[How to read and zoom the figures](../diagrams/README.md#reading-size).

## Reading guide

| Step | Page |
|---|---|
| Platform and information flow | [LLM and agent platform](llm_platform.md) |
| Inference implementation | [CPU inference through llm-d](llm_inference_platform.md) |
| Shared model settings | [Shared model configuration](global_model_config.md) |
| RAG data pipeline | [Airflow RAG pipeline and DataHub governance](rag_orchestration.md) |
| MCP tools and contracts | [Machine and knowledge tools over MCP](mcp_tools.md) |
| Specialists and coordination | [GPU inference and verified agent delegation](gpu_agent_routing.md) |
| Metrics, logs and traces | [Measuring model and agent activity](observability.md) |
| Post-restart acceptance | [Post-restart agent validation](agent_runtime_validation.md) |

## Reference pages

Open a group for its full component and experiment index. Commands run from the repository root; linked receipts describe their recorded workloads.

<details>
<summary>Delivery, infrastructure and security · 6 pages</summary>

- [Admit agent capabilities before deployment](agent_admission_policy.md)
- [Private gateway for the IoT agents](agent_gateway.md)
- [Agent network isolation](agent_network_isolation.md)
- [Delivering MCP services and IoT agents](ci_agents.md)
- [Infrastructure behind the agent platform](llm_infrastructure_evidence.md)
- [Centralized agent secrets in Vault](vault_agent_ui.md)

</details>

<details>
<summary>Observability and controlled experiments · 6 pages</summary>

- [Comparing agent configurations](agent_configuration_ab.md)
- [Agent warm-up: measured results and availability limits](agent_warmup.md)
- [Comparing two model artifacts](llm_model_ab.md)
- [Demonstrate the model and agent monitoring](llm_monitoring_demo.md)
- [Counting prompts caught by the policy](llm_policy_window.md)
- [Measuring model and agent activity](observability.md)

</details>

<details>
<summary>Agents, application design and review · 11 pages</summary>

- [A notebook that calls the deployed agents](agent_notebook.md)
- [Native agent control plane and catalog](agent_platform.md)
- [Complete agent answers](agent_response_contract.md)
- [Recovering the retained agent lab](agent_restart_recovery.md)
- [Post-restart agent validation](agent_runtime_validation.md)
- [Agent worker scaling and admission backpressure](agent_scaling.md)
- [Trace the data behind an assistant answer](assistant_data_validation.md)
- [GPU inference and verified agent delegation](gpu_agent_routing.md)
- [Five classes behind the assistant's data and answers](llm_class_design.md)
- [LLM and agent platform](llm_platform.md)
- [Final LLM rubric tracker](rubric_tracker.md)

</details>

<details>
<summary>Knowledge and RAG · 5 pages</summary>

- [RAG delivery through Jenkins and Airflow](ci_rag.md)
- [Grounded answers with traceable references](llm_grounding.md)
- [Review knowledge changes before publication](rag_corpus_drift.md)
- [Versioned knowledge ingestion and native retrieval](rag_foundation.md)
- [Airflow RAG pipeline and DataHub governance](rag_orchestration.md)

</details>

<details>
<summary>Inference and shared configuration · 3 pages</summary>

- [Shared model configuration](global_model_config.md)
- [GPU inference experiment](gpu_inference.md)
- [CPU inference through llm-d](llm_inference_platform.md)

</details>

<details>
<summary>MCP tools and API quality · 5 pages</summary>

- [MCP application factory and dependency injection](mcp_application_design.md)
- [Private HTTPS for retrieval tools](mcp_gateway.md)
- [MCP contract and mutation checks](mcp_quality.md)
- [MCP API testing: results and techniques](mcp_quality_evidence.md)
- [Machine and knowledge tools over MCP](mcp_tools.md)

</details>

## Evidence boundaries

[The requirement tracker](rubric_tracker.md) links the original requirements to scoped native results. Screenshots, failed attempts and measurements keep their recorded identities. Offline artifact checks do not establish current service readiness, mentor acceptance or a grade.
