# Rubric implementation and evidence map

Both final ML and final LLM/agents are selected. The tables distinguish recorded ML/LLM milestones from remaining agent work. Source weights are not self-awarded grades. [Source notes](rubric-source.md) preserve the original workbook/cell references.

## Final ML

| ID | Requirement | Code entry | Evidence | Offline checker |
|---|---|---|---|---|
| F03 | Use FastAPI, Pydantic data validation and Kubernetes health checks for the feature-retrieval API. | [app](../services/feature_api/app.py) | [feature api](feature-api.md); [api deployment](api-deployment.md) | native-serving |
| F04 | Use asynchronous request handling in the feature-retrieval API. | [app](../services/feature_api/app.py) | [app](../services/feature_api/app.py); [api deployment](api-deployment.md) | native-serving |
| F05 | Deploy the feature-retrieval API to Kubernetes using Helm, rolling updates and automatic fallback, such as --atomic. | [app](../services/feature_api/app.py) | [api deployment](api-deployment.md) | native-serving |
| F06 | Use FastAPI, Pydantic data validation and Kubernetes health checks for the real-time drift API. | [app](../services/drift_api/app.py) | [drift detection](drift-detection.md); [api deployment](api-deployment.md) | drift-api capsule / drift-eventing |
| F07 | Use asynchronous request handling in the real-time drift API. | [app](../services/drift_api/app.py) | [app](../services/drift_api/app.py) | drift-api capsule / drift-eventing |
| F08 | Deploy the real-time drift API to Kubernetes using Helm, rolling updates and automatic fallback. | [app](../services/drift_api/app.py) | [api deployment](api-deployment.md) | drift-api capsule / drift-eventing |
| F09 | Demonstrate autoscaling of the feature-retrieval API. | [hpa](../infra/charts/iot-api/templates/hpa.yaml) | [api autoscaling](api-autoscaling.md) | api-autoscale capsule |
| F10 | Demonstrate autoscaling of the drift-detection API. | [hpa](../infra/charts/iot-api/templates/hpa.yaml) | [api autoscaling](api-autoscaling.md) | api-autoscale capsule |
| F11 | Unit-test the Web APIs with Test Coverage > 90%, demonstrating fixtures and mocks with screenshots. | [test api observability](../scripts/test-api-observability.py) | [ci serving](ci-serving.md); [drift detection](drift-detection.md) | native-serving / ci-api-observability |
| F12 | Use equivalence partitioning and boundary-value analysis to design parameterized test cases. | [run focused api validation](../scripts/run-focused-api-validation.py) | [api validation](api-validation.md) | focused-api |
| F13 | Use mutation testing to assess test effectiveness. Mutate changed code rather than the entire codebase on every commit. Mutation score > 80%. | [test api observability](../scripts/test-api-observability.py) | [ci serving](ci-serving.md); [drift detection](drift-detection.md) | native-serving / ci-api-observability |
| F14 | Use property-based idempotency tests, for example repeated model-prediction consistency. Hypothesis and its CrossHair backend are suggested tools. | [run focused api validation](../scripts/run-focused-api-validation.py) | [api validation](api-validation.md) | focused-api |
| F15 | Load-test the feature-retrieval Web API for throughput and latency; produce a Locust HTML report documenting the SLA workload. | [feature_api](../tests/load/feature_api.py) | [ci serving](ci-serving.md); [observed api load](observed-api-load.md) | observed-api-load |
| F16 | Simulate data drift. | [drift](../generator/drift.py) | [drift detection](drift-detection.md) | drift-api capsule |
| F17 | Control drift through generator configuration. | [drift](../generator/drift.py) | [sensor drift small](../generator/configs/sensor-drift-small.json) | drift-api capsule |
| F18 | Create a separate two-column ID/label table for joining with feature tables during training; domain-specific ID names are allowed. | [drift](../generator/drift.py) | [drift detection](drift-detection.md); [training notebook](training-notebook.md) | drift-api capsule |
| F19 | Build an incremental pipeline, for example Airflow, materializing the latest offline features into the online store. | [batch](../feature_store/batch.py) | [feature jobs and ttl](feature-jobs-and-ttl.md); [final handover](final-handover.md#feature-pipelines) | final-handover / ci-materialization capsule |
| F20 | Deploy a job pushing the mini-coursework streaming features into the OFFLINE store. | [staged_push](../feature_store/staged_push.py) | [ci stream push](ci-stream-push.md); [final handover](final-handover.md#feature-pipelines) | window-cohort / ci-stream-push |
| F21 | Deploy a job pushing the mini-coursework streaming features into the ONLINE store. | [staged_push](../feature_store/staged_push.py) | [ci stream push](ci-stream-push.md); [final handover](final-handover.md#feature-pipelines) | window-cohort / ci-stream-push |
| F22 | Define and justify the TTL of each feature table. | [repository](../feature_store/repository.py) | [feature jobs and ttl](feature-jobs-and-ttl.md) | final-handover / feature-jobs-and-ttl.md |
| F23 | Execute a Jupyter notebook that retrieves offline data through Feast, joins the generated labels, splits train/validation, fits a task-appropriate ML/DL model, evaluates validation and saves the model. | [01_failure_training](../notebooks/01_failure_training.ipynb) | [training notebook](training-notebook.md) | notebook / notebook-parity capsules |
| F24 | Convert the notebook into a Kubeflow training pipeline with the same number of ML steps; standalone Kubeflow Pipelines is sufficient. | [kfp_notebook](../ml/kfp_notebook.py) | [training notebook](training-notebook.md); [distributed training](distributed-training.md) | distributed-training capsule / ci-training |
| F25 | Use distributed training in the training step; distributed XGBoost is an example, not a required model. | [kfp_notebook](../ml/kfp_notebook.py) | [distributed training](distributed-training.md) | distributed-training capsule / ci-training |
| F26 | After training completes, store model weights and hyperparameters in a model registry such as MLflow. | [run versioned training](../scripts/run-versioned-training.py) | [automatic ml versioning](automatic-ml-versioning.md) | automatic-versioning capsule / ci-training |
| F27 | Version each Feast training-data retrieval incrementally, storing changed data rather than duplicating every full dataset. | [snapshot](../ml/snapshot.py) | [automatic ml versioning](automatic-ml-versioning.md); [incremental data versioning](incremental-data-versioning.md) | data-versions / automatic-versioning capsules |
| F28 | Provide CI/CD for the materialization pipeline. | [ci materialization](../scripts/ci-materialization.py) | [ci materialization](ci-materialization.md) | ci-materialization capsule |
| F29 | Provide CI/CD for the training pipeline. | [ci training](../scripts/ci-training.py) | [ci training](ci-training.md) | ci-training |
| F30 | Provide CI/CD for DP1 as defined in the mini-coursework. | [ci data pipelines](../scripts/ci-data-pipelines.py) | [ci data pipelines](ci-data-pipelines.md) | ci-data-pipelines capsule |
| F31 | Provide CI/CD for DP2 as defined in the mini-coursework. | [ci data pipelines](../scripts/ci-data-pipelines.py) | [ci data pipelines](ci-data-pipelines.md) | ci-data-pipelines capsule |
| F32 | Provide CI/CD for DP3 as defined in the mini-coursework. | [ci data pipelines](../scripts/ci-data-pipelines.py) | [ci data pipelines](ci-data-pipelines.md) | ci-data-pipelines capsule |
| F33 | Provide CI/CD for the FastAPI Web API, retrieving the production/tagged-production model from the registry before packaging and deployment. | [ci serving](../scripts/ci-serving.py) | [ci serving](ci-serving.md); [active feature refresh](active-feature-refresh.md) | native-serving / ci-api-observability |
| F34 | Provide CI/CD for the inference engine, for example KServe. | [ci serving](../scripts/ci-serving.py) | [ci serving](ci-serving.md); [ci api observability](ci-api-observability.md) | native-serving / ci-api-observability |
| F35 | Provide CI/CD for the real-time drift Web API using Knative Eventing with KServe. | [ci drift eventing](../scripts/ci-drift-eventing.py) | [drift eventing](drift-eventing.md) | drift-eventing |
| F36 | Provide CI/CD for the job pushing stream features to the OFFLINE store. | [ci stream push](../scripts/ci-stream-push.py) | [ci stream push](ci-stream-push.md) | ci-stream-push |
| F37 | Provide CI/CD for the job pushing stream features to the ONLINE store. | [ci stream push](../scripts/ci-stream-push.py) | [ci stream push](ci-stream-push.md) | ci-stream-push |
| F38 | Place the metrics-viewing service, for example Grafana, behind the NGINX gateway. | [deploy private gateway](../scripts/deploy-private-gateway.py) | [private gateway](private-gateway.md) | private-gateway |
| F39 | Place the log-viewing service behind the gateway. | [deploy private gateway](../scripts/deploy-private-gateway.py) | [private gateway](private-gateway.md) | private-gateway |
| F40 | Place the trace-viewing service behind the gateway. | [deploy private gateway](../scripts/deploy-private-gateway.py) | [private gateway](private-gateway.md) | private-gateway |
| F41 | Place the feature-retrieval Web API behind the gateway. | [deploy private gateway](../scripts/deploy-private-gateway.py) | [private gateway](private-gateway.md) | private-gateway |
| F42 | Configure gateway Basic authentication and rate limiting for the feature-retrieval Web API. | [deploy private gateway](../scripts/deploy-private-gateway.py) | [private gateway](private-gateway.md) | private-gateway |
| F43 | Configure a domain and HTTPS for the feature-retrieval Web API. | [deploy private gateway](../scripts/deploy-private-gateway.py) | [private gateway](private-gateway.md) | private-gateway |
| F44 | Use Terraform to provision GKE or other cloud services, with a suitable service/module directory layout. | [main](../infra/gcp/vm/main.tf) | [iac verification](iac-verification.md) | iac-final |
| F45 | Use Ansible roles to configure the VM and deploy services. | [bootstrap](../infra/ansible/bootstrap.yml) | [iac verification](iac-verification.md) | iac-final |
| F46 | Monitor Web API request rate, request counts, failures and related metrics. | [ci observability](../scripts/ci-observability.py) | [api observability](api-observability.md); [observed api load](observed-api-load.md) | api-observability |
| F47 | Collect and visualize compute telemetry such as CPU, RAM, disk and network through Prometheus/Grafana or equivalent tools. | [host_monitoring](../ml/host_monitoring.py) | [host monitoring](host-monitoring.md) | host-monitoring / linux-host |
| F48 | Collect and visualize logs. | [log_discovery](../ml/log_discovery.py) | [log monitoring](log-monitoring.md); [log discovery](log-discovery.md) | linux-logs / log-discovery |
| F49 | Collect and visualize traces. | [ci observability](../scripts/ci-observability.py) | [api observability](api-observability.md) | api-observability |
| F50 | Schedule an Airflow drift pipeline to retrieve offline features, compare with its reference and update Grafana through Pushgateway. The source explicitly assumes ground-truth labels are unavailable for drift monitoring. | [verify offline drift](../scripts/verify-offline-drift.py) | [offline drift monitoring](offline-drift-monitoring.md) | offline-drift |
| F51 | Trigger retraining by calling the Kubeflow API, for example from a final step in the Airflow drift pipeline. | [verify drift retraining](../scripts/verify-drift-retraining.py) | [drift retraining](drift-retraining.md) | drift-retraining |
| F52 | Split A/B traffic between two inference-service versions deployed through CI/CD. | [ci serving](../scripts/ci-serving.py) | [ci ab deployment](ci-ab-deployment.md) | native-serving / ab-monitoring |
| F53 | Monitor both inference versions on a dashboard; the source assumes no ground-truth labels for drift monitoring. | [ab_monitoring](../ml/ab_monitoring.py) | [ab monitoring](ab-monitoring.md) | ab-monitoring |
| F54 | Centralize shared secret management through HashiCorp Vault or an equivalent system. | [vault_client](../infra/security/vault_client.py) | [vault](vault.md) | vault / final-review |
| F55 | Authorize service-to-service access using a service mesh. | [install private mesh](../scripts/install-private-mesh.py) | [private mesh](private-mesh.md) | private-mesh |
| F56 | Keep clean code and a clean repository, and demonstrate a design pattern in the implementation. | [runtime](../services/feature_api/runtime.py) | [ml class design](ml-class-design.md); [repository quality](repository-quality.md) | final-handover / repository-quality.md |
| F57 | Document five key classes expressing the main ML responsibilities, including interfaces such as training-data validation/deduplication and time-based splitting. | [ml class design](../docs/ml-class-design.md) | [ml class design](ml-class-design.md) | five classes/design pattern document |
| F58 | Document novel idea 1 and demonstrate it works; it may be a researched technique/tool not taught in EDAI. | [joint_drift](../ml/joint_drift.py) | [joint drift](joint-drift.md); [joint cohort](joint-cohort.md) | joint-cohort |
| F59 | Document novel idea 2 and demonstrate it works; it may be a researched technique/tool not taught in EDAI. | [check deployment policy](../scripts/check-deployment-policy.py) | [deployment policy](deployment-policy.md); [learner confirmation](evidence/course-scope/learner-confirmation.json) | deployment-policy |

The detailed status and source deliverables are in the [ML tracker](final-rubric-tracker.md). Recorded A2 evidence includes the README, deployment figures, repository layout and documented code.

## Final LLM/agents

| ID | Group | Requirement | Status |
|---|---|---|---|
| L03 | LLM inference platform | Deploy an LLM inference platform. | [Recorded native inference](llm-inference.md) |
| L04 | LLM inference platform | Configure a custom model server. | [Recorded native inference](llm-inference.md) |
| L05 | LLM inference platform | Benchmark the model server and optimize the inference platform. | [Recorded native inference](llm-inference.md) |
| L06 | Global model configuration | [implementation](../infra/agents/kagent-values.yaml) | [Native platform checkpoint](agent-platform.md) | agent-platform; full chat/scale/boundary/benchmark scope remains open |
| L07 | Agent registry | [implementation](../infra/agents/registry-values.yaml) | [Native platform checkpoint](agent-platform.md) | agent-platform; full chat/scale/boundary/benchmark scope remains open |
| L08 | RAG pipeline | Build an Airflow RAG pipeline with text retrieval, chunking and chunk-embedding storage in a feature store through Feast. | Recorded native RAG evidence; [report](rag-orchestration.md) |
| L09 | RAG pipeline | Provide data governance for the RAG pipeline as in the mini-coursework. | Recorded native RAG evidence; [report](rag-orchestration.md) |
| L10 | Retrieval MCP and agent | Use FastAPI, Pydantic validation and Kubernetes health checks for the entity/chunk retrieval Web API. | [Recorded native MCP/API scope](mcp-tools.md); agent integration remains open |
| L11 | Retrieval MCP and agent | Use asynchronous request handling in the entity/chunk retrieval API. | [Recorded native MCP/API scope](mcp-tools.md); agent integration remains open |
| L12 | Retrieval MCP and agent | Deploy the retrieval API as an MCP tool to Kubernetes with Helm, rolling updates and automatic fallback; follow MCP-specific deployment guidance. | [Recorded native MCP/API scope](mcp-tools.md); agent integration remains open |
| L13 | Retrieval MCP and agent | [scaler chart](../infra/charts/iot-agent-scaling/templates/scaledobjects.yaml) | [Native answers, 2 → 4 → 2 scaling and original UI](agent-scaling.md) | gpu-agents / agent-scaling |
| L14 | Retrieval MCP and agent | [worker policy](../infra/charts/iot-agent-network/templates/network.yaml) | [Native direct MCP boundary](agent-network-isolation.md); [original UI](agent-scaling.md#native-ui-evidence) | agent-network / agent-scaling; wider security limits remain explicit |
| L15 | Retrieval MCP and agent | [catalog publication](../scripts/publish-iot-agents.py) | [Native source-bound catalog](gpu-agent-routing.md); [original UI](agent-scaling.md#native-ui-evidence) | gpu-agents / agent-scaling |
| L16 | Drift MCP and agent | Use FastAPI, Pydantic validation and Kubernetes health checks for the drift-detection MCP API. | [Recorded native MCP/API scope](mcp-tools.md); agent integration remains open |
| L17 | Drift MCP and agent | Use asynchronous request handling in the drift-detection MCP API. | [Recorded native MCP/API scope](mcp-tools.md); agent integration remains open |
| L18 | Drift MCP and agent | Deploy the drift API as an MCP tool to Kubernetes with Helm, rolling updates and automatic fallback; follow MCP-specific deployment guidance. | [Recorded native MCP/API scope](mcp-tools.md); agent integration remains open |
| L19 | Drift MCP and agent | [scaler chart](../infra/charts/iot-agent-scaling/templates/scaledobjects.yaml) | [Native drift answer, scaling and original UI](agent-scaling.md) | gpu-agents / agent-scaling |
| L20 | Drift MCP and agent | [worker policy](../infra/charts/iot-agent-network/templates/network.yaml) | [Native direct MCP boundary](agent-network-isolation.md); [original UI](agent-scaling.md#native-ui-evidence) | agent-network / agent-scaling; wider security limits remain explicit |
| L21 | Drift MCP and agent | [catalog publication](../scripts/publish-iot-agents.py) | [Native source-bound catalog](gpu-agent-routing.md); [original catalog UI](agent-scaling.md#native-ui-evidence) | gpu-agents / agent-scaling |
| L22 | Agent notebooks | [Native Jupyter execution, source and original captures](agent-notebook.md) | agent-notebook |
| L23 | Agent notebooks | [Native Jupyter execution, source and original captures](agent-notebook.md) | agent-notebook |
| L24 | Coordinator agent | [scaler chart](../infra/charts/iot-agent-scaling/templates/scaledobjects.yaml) | [18 actual parents, 36 child histories and scaling](agent-scaling.md) | gpu-agents / agent-scaling |
| L25 | Coordinator agent | [catalog publication](../scripts/publish-iot-agents.py) | [Native coordinator catalog](gpu-agent-routing.md); [original catalog and answer UI](agent-scaling.md#native-ui-evidence) | gpu-agents / agent-scaling |
| L26 | Warm-up and worker pool | [native benchmark](../scripts/benchmark-agent-warmup.py) | [Matched timings and HA limits](agent-warmup.md) | agent-warmup; mixed results retained, no general startup or host-HA claim |
| L27 | Validation and verification | Unit-test the Web APIs with Test Coverage > 90%, demonstrating fixtures and mocks with screenshots. | [Recorded native MCP/API scope](mcp-tools.md); agent integration remains open |
| L28 | Validation and verification | Use equivalence partitioning and boundary-value analysis to design parameterized test cases. | [Recorded native MCP/API scope](mcp-tools.md); agent integration remains open |
| L29 | Validation and verification | Use mutation testing on changed code rather than the entire codebase on every commit. Mutation score > 80%. | [Complete local gate 80.12%; original VM failures retained](mcp-quality.md); screenshot/CI integration remains open |
| L30 | Validation and verification | Use property-based idempotency tests, with Hypothesis/CrossHair suggested for finding inconsistent repeated behavior. | [Recorded native MCP/API scope](mcp-tools.md); agent integration remains open |
| L31 | Validation and verification | Load-test the data-retrieval Web API for throughput and latency and produce a Locust HTML SLA report. | [Recorded native MCP/API scope](mcp-tools.md); agent integration remains open |
| L32 | Generator | Simulate data drift. | Open: native LLM evidence missing |
| L33 | Generator | Control drift through generator configuration. | Open: native LLM evidence missing |
| L34 | Generator | Create a separate two-column ID/label table for joining with training features. | Open: native LLM evidence missing |
| L35 | CI/CD | Provide test/build/auto-deploy CI/CD for the RAG data pipeline. | Open: native LLM evidence missing |
| L36 | CI/CD | Provide CI/CD for the data-retrieval MCP server and corresponding agent. | Open: native LLM evidence missing |
| L37 | CI/CD | Provide CI/CD for the drift-detection MCP server and corresponding agent. | Open: native LLM evidence missing |
| L38 | CI/CD | Provide CI/CD for the coordinator agent. | Open: native LLM evidence missing |
| L39 | CI/CD | Provide CI/CD for the stream-feature OFFLINE-store job. | Open: native LLM evidence missing |
| L40 | CI/CD | Provide CI/CD for the stream-feature ONLINE-store job. | Open: native LLM evidence missing |
| L41 | Routing and gateway | Place the metrics-viewing service behind the NGINX gateway. | Open: native LLM evidence missing |
| L42 | Routing and gateway | Place the log-viewing service behind the gateway. | Open: native LLM evidence missing |
| L43 | Routing and gateway | Place the trace-viewing service behind the gateway. | Open: native LLM evidence missing |
| L44 | Routing and gateway | Place the agent-testing UI behind the gateway. | Open: native LLM evidence missing |
| L45 | Routing and gateway | Place the agent-registry UI behind the gateway. | Open: native LLM evidence missing |
| L46 | Routing and gateway | Configure authentication and gateway rate controls for the agent-testing UI. | Open: native LLM evidence missing |
| L47 | Routing and gateway | Configure a domain and HTTPS for the data-retrieval Web API. | Open: native LLM evidence missing |
| L48 | Infrastructure as code | Use Terraform to provision GKE or other cloud services with a suitable directory layout. | Open: native LLM evidence missing |
| L49 | Infrastructure as code | Use Ansible roles to configure the VM and deploy services. | Open: native LLM evidence missing |
| L50 | Observability | Monitor Web API request rate, request counts, failures and related metrics. | [Partial: MCP counters](mcp-tools.md); agent/dashboard export open |
| L51 | Observability | Collect and visualize CPU, RAM, disk and network metrics through Prometheus/Grafana or equivalent tools. | Open: native LLM evidence missing |
| L52 | Observability | Collect and visualize logs. | Open: native LLM evidence missing |
| L53 | Observability | Collect and visualize traces. | Open: native LLM evidence missing |
| L54 | Observability | Collect at least input/output/total tokens per request, full generation round-trip time, TTFT and counts/frequency of prompts caught by safety or PII controls. | Open: native LLM evidence missing |
| L55 | Observability | Collect at least per-agent call counts, per-MCP-tool call counts and failures for each tool call. | [Partial: MCP counters](mcp-tools.md); agent/dashboard export open |
| L56 | A/B testing | A/B-test agents using different model configurations and monitor both variants. | Open: native LLM evidence missing |
| L57 | A/B testing | A/B-test different LLM versions on the inference platform. | Open: native LLM evidence missing |
| L58 | Security | Centralize shared secret management through HashiCorp Vault or an equivalent system. | Open: native LLM evidence missing |
| L59 | Repository design | Keep clean code and repository structure and demonstrate an implemented design pattern. | Open: native LLM evidence missing |
| L60 | Class design | Document five key classes expressing the main ML responsibilities; retain the source requirement and its example interfaces. | Open: native LLM evidence missing |
| L61 | Novel ideas | Document novel idea 1 and demonstrate it works; it may be a researched technique/tool not taught in EDAI. | Open: native LLM evidence missing |
| L62 | Novel ideas | Document novel idea 2 and demonstrate it works; it may be a researched technique/tool not taught in EDAI. | Open: native LLM evidence missing |

See the [LLM tracker](final-llm-rubric-tracker.md) for exact source cells and deliverables, and the [implementation plan](llm-agent-plan.md) for acceptance evidence. Existing ML infrastructure is a baseline, not proof of agent deployment.
