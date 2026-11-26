# LLM and agent platform

[Documentation](../README.md) · [Data and ML](../edai-1/README.md)

The assistant helps a technician investigate simulated industrial pumps. You can ask “How high is the failure risk for P-001, and what should I compare with its vibration readings?” The coordinator delegates the investigation. The retrieval specialist calls tools for the pump's sensor summary, model score and relevant passages. For a cohort investigation, the drift specialist requests a comparison against the training reference for the served model.

The Feature API and KServe provide the failure-risk score. RAG Search API provides passages from the indexed simulator documents, with their source identities. The language model on the private GPU helps the agents choose tools and explain the results. [Current ML and LLM diagram](../architecture/ml-llm-platform-v19-notes.md) · [One recorded agent answer](agent_runtime_validation.md)

## Reading guide

| Question | Where to look |
|---|---|
| How does one question become a verified answer? | [Follow a pump investigation](llm_platform.md) · [Recorded task and tool results](agent_runtime_validation.md) |
| Which services supply the readings, score, documents and drift result? | [Tool backends and current routing](mcp_tools.md) |
| Where do the document passages come from? | [Airflow indexing, validation and DataHub lineage](rag_orchestration.md) |
| Where does the language model run, and who routes the calls? | [Private GPU inference](llm_inference_platform.md) · [Agentgateway and A2A](global_model_config.md) |
| How are request flow and capacity checked? | [Metrics, logs and traces](observability.md) · [Worker autoscale](agent_scaling.md) |
| How are new versions verified before use? | [MCP and agent delivery](ci_agents.md) · [RAG delivery](ci_rag.md) |

## Reference pages

Open a group to find original experiments and supporting implementation records. The reading guide above groups the main review topics.

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
