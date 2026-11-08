# IoT Maintenance Platform

Data pipelines, failure prediction and tool-grounded answers for industrial pumps.

The platform connects pump telemetry to validated features, versioned models and API predictions. Its agents use the same machine context, technical references and drift diagnostics to investigate equipment health with traceable sources.

The reference deployment uses Google Cloud VMs and K3s, with a GPU-backed model server for agents. Equipment, sensor readings and failure events are simulated for reproducible evaluation.

## Contents

- [Business domain](#business-domain)
- [Architecture](#architecture): [data](#data-platform), [ML](#ml-platform), [LLM and agents](#llm-and-agent-platform), [deployment](#deployment)
- [Recorded results](#recorded-results)
- [Quick start](#quick-start)
- [Repository structure](#repository-structure)
- [Documentation](#documentation)

## Business domain

Maintenance staff need to inspect the condition of an operating pump and identify machines that may fail soon. Temperature, vibration, motor current and RPM provide a history of wear and load; failures and maintenance define when a machine is unavailable.

| Data | Grain | Main issue |
|---|---|---|
| Equipment metadata | Effective-dated machine attributes | Historical changes and uneven factory sizes |
| Telemetry | Machine at event time | Duplicate exports, schema changes, skew and delayed arrival |
| Stream deliveries | One delivery attempt | Retries, out-of-order arrival and late events |
| Failures and maintenance | One recorded onset or intervention interval | Downtime, delayed recording and incomplete future observation |
| Technical references | Versioned authored document/chunk | Retrieval ordering, source identity and corpus changes |

The prediction target is `failure_in_next_1h`: a failure starts in **(t, t + 1 hour]** for a machine operating at t. Features must have arrived by the prediction time. Failed or maintenance-state machines are excluded, and unobserved futures are not treated as negative examples. See the [data contract](docs/edai-1/data_contract.md) and [evaluation protocol](docs/edai-1/ml_evaluation_protocol.md).

## Architecture

The architecture groups the platform by responsibility and shows the data, API calls and release decisions between its components. The detailed views below follow each path through its handlers, jobs and outputs.

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/images/platform-architecture.png">
  <img alt="IoT Maintenance Platform architecture" src="docs/images/platform-architecture.gif">
</picture>

[Full-size SVG](docs/images/platform-architecture.svg) · [Static PNG](docs/images/platform-architecture.png) · [Editable Excalidraw](docs/diagrams/platform-architecture.excalidraw) · [Motion SVG](docs/images/platform-architecture.animated.svg) · [Source decisions](docs/diagrams/execution-guide.md#main-platform-architecture)

Moving dots illustrate call and data direction. They are independent loops, not live traffic or pipeline timing.

| Flow | View | Entry and result |
|---|---|---|
| B1 | [Batch ingest and release](docs/images/execution-batch.png) | Airflow REST trigger → validated Bronze, Silver, Gold, features and labels |
| S1 | [Streaming and feature delivery](docs/images/execution-stream.png) | Bounded Kafka replay → Flink windows, audit and late data → separate CI delivery |
| T1 | [Training and release](docs/images/execution-training.png) | Private KFP submission → evaluation and registration → independent serving build |
| P1 | [Prediction](docs/images/execution-prediction.png) | Machine-ID POST → eligibility and KServe → checked score or HTTP error |
| K1 | [Knowledge indexing](docs/images/execution-knowledge.png) | Airflow run ID → sealed snapshot → separate Retrieval MCP rollout |
| A1 | [Agent request](docs/images/execution-agents.png) | A2A message → isolated children, tools and generation → returned A2A task |
| D1 | [Deployment](docs/images/execution-deployment.png) | Private entry, CPU and GPU placement, Services, ports and volumes |

The [execution guide](docs/diagrams/execution-guide.md) distinguishes implemented code, configured activation and recorded runtime evidence. The [diagram inventory](docs/diagrams/README.md) keeps the earlier views and interactive viewer available for comparison.

## Data platform

| Stage | Input → output | Implementation / validation |
|---|---|---|
| Source generation | Equipment history → telemetry, failures, maintenance and delivery attempts | [Generator](generator/README.md), [data checks](docs/edai-1/chunked_generator.md) |
| DP1 | Offline source → Bronze | [Chunk ingestion and readback](docs/edai-1/processing_jobs.md#chunk-ingest) |
| DP2 | Bronze → cleaned Silver and dimensional Gold | [Schema, deduplication and skew](docs/edai-1/processing_jobs.md#batch-corrections) |
| DP3 | Eligible history → features and separate target records | [Feature construction](docs/edai-1/processing_jobs.md#bounded-features) |
| Streaming | Arrival-ordered Kafka deliveries → finalized Flink windows | [Window code and late arrivals](docs/edai-1/processing_jobs.md#flink-window-processing-code) |
| Orchestration and governance | Validated outputs → scheduled publication, schemas, lineage and assertions | [Airflow and DataHub](docs/edai-1/processing_jobs.md#orchestration) |
| Storage | Layered Parquet, Gold SQL queries and Feast projects | [Storage implementation and measured changes](docs/edai-1/data_storage.md) |

### ML platform

The notebook and Kubeflow pipeline use the same five-step ML workflow. Training uses a temporal split, train-only transformations and a separately recorded evaluation protocol. MLflow links dataset versions, runs and model artifacts. The Feature API retrieves online values, checks eligibility/provenance and calls a pinned KServe model.

| Flow | Main components | Detail |
|---|---|---|
| Training | Feast historical retrieval → validation → temporal split → distributed training → saved-model evaluation | [Notebook](docs/edai-1/training_notebook.md), [distributed run](docs/edai-1/distributed_training.md), [five classes](docs/edai-1/ml_class_design.md) |
| Versioning | Incremental data identities, training runs and model registry | [Data/model versions](docs/edai-1/automatic_ml_versioning.md) |
| Prediction | Machine ID → Feast online trace → KServe score → versioned response | [Services](services/README.md), [Feature API](docs/edai-1/feature_api.md) |
| Monitoring | Input drift → guarded retraining request, with replay/cooldown checks | [Drift and retraining](docs/edai-1/drift_retraining.md) |
| Delivery | Source-bound tests/build → Helm rollout → verification/rollback | [Serving CI](docs/edai-1/ci_serving.md), [A/B deployment](docs/edai-1/ci_ab_deployment.md) |

### LLM and agent platform

The retrieval specialist obtains machine features, the model score and cited reference chunks. The drift specialist compares a bounded machine cohort with a pinned reference. The coordinator preserves their returned values and identifiers. There is no physical actuation tool.

<details>
<summary>Agent flow — delegation, actual tool facts and shared GPU inference</summary>

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/images/execution-agents.png">
  <img alt="Coordinator child A2A through Agentgateway, scoped MCP aliases, shared inference and verified child results" src="docs/images/execution-agents.gif">
</picture>

[Full-size PNG](docs/images/execution-agents.png) · [Execution and source contracts](docs/diagrams/execution-guide.md)

</details>

[Excalidraw source](docs/diagrams/flow-agents.excalidraw) · [SVG](docs/images/flow-agents.svg) · [LLM platform](docs/edai-2/llm_platform.md)

| Path | What happens | Evidence |
|---|---|---|
| Reference ingestion | Documents → chunks → pinned embeddings → versioned Feast/Milvus snapshot | [Six-task RAG pipeline](docs/edai-2/rag_orchestration.md) |
| Agent question | Private UI → coordinator → two specialists → authenticated MCP services | [Expanded specialist tool chats](docs/edai-2/agent_platform.md#native-specialist-tools), [gateway delegation proof](docs/edai-2/global_model_config.md#native-results) |
| Generation | Shared ModelConfig → Agentgateway → llm-d → observer/tool-turn adapter → llama.cpp on the private V100 | [Setup, model call and measured inference](docs/edai-2/llm_inference_platform.md) |
| Worker capacity | gVisor SandboxAgents and three WorkerPools, with measured 2 → 4 → 2 scaling | [Scaling](docs/edai-2/agent_scaling.md), [startup study](docs/edai-2/agent_warmup.md) |
| Delivery and controls | Separate RAG/retrieval/drift/coordinator releases, scoped credentials and bounded gateway access | [CI/CD](docs/edai-2/ci_agents.md), [gateway](docs/edai-2/agent_gateway.md), [Vault](docs/edai-2/vault_agent_ui.md) |
| Evaluation | Original replies, child/tool histories, token/TTFT telemetry and small paired comparisons | [Monitoring](docs/edai-2/llm_monitoring_demo.md), [model A/B](docs/edai-2/llm_model_ab.md), [agent A/B](docs/edai-2/agent_configuration_ab.md) |

The [controlled retrieval comparison](docs/edai-2/agent_warmup.md#retrieval-with-and-without-substrate) measured 6.497 s with Substrate versus 4.234 s for the ordinary Agent. The slower result is retained; a general latency or cloud-cost benefit is not claimed.

The two researched LLM extensions are [OPA capability/budget admission](docs/edai-2/agent_admission_policy.md) and [document-weighted corpus MMD review](docs/edai-2/rag_corpus_drift.md). Each has accepted and intentionally rejected native cases, with its limits documented.

### Deployment

The control/data lab runs on an **8-vCPU / 64-GiB CPU VM**. A separate private **4-vCPU / 15-GiB worker with a 16-GiB V100** serves the LLM. K3s hosts Kubernetes workloads; retained host services and Docker containers supply the remaining components. Administration uses IAP and loopback ports, with authenticated CA-verified HTTPS on the private gateway hop.

<details>
<summary>Deployment topology — hosts, workloads and access boundaries</summary>

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/images/deployment-runtime.png">
  <img alt="Operator loopback and IAP access, CPU K3s Docker and host workloads, persistent volumes and the GPU worker in the same cluster" src="docs/images/deployment-runtime.gif">
</picture>

[Full-size PNG](docs/images/deployment-runtime.png) · [Zoomable local viewer](docs/diagrams/architecture.html#view=deployment-runtime)

</details>

[Excalidraw source](docs/diagrams/deployment-runtime.excalidraw) · [SVG](docs/images/deployment-runtime.svg) · [Infrastructure](docs/edai-2/llm_infrastructure_evidence.md) · [Operations](docs/edai-1/operations.md)

Deployment frames mark physical hosts and runtimes. Named boxes group selected related resources; a WorkerPool owns worker Pods, and the observer Deployment contains two containers. The [deployment crosswalk](docs/diagrams/README.md#deployment-groups) maps the earlier 42-resource inventory into these groups. Supporting services remain in the [runtime configuration](docs/edai-1/architecture.md#deployment-and-security). The drawings describe the recorded lab and its connection boundaries; they do not establish simultaneous execution or current endpoint readiness. Two executor Pods on one physical CPU host do not establish multi-host high availability.

## Recorded results

The [mentor review follow-up](docs/mentor_review_followup.md) links the updated chapters, measured benchmark and native Agentgateway run. The [earlier data-platform checklist](docs/edai-1/data_review_audit.md) preserves its original evidence mapping.

| Workload | Recorded result | Report |
|---|---|---|
| Large ingestion | 102,447,847,856 uncompressed JSONL bytes; 316.8 million unique Silver rows; 13 Spark checks | [Full-scale ingest](docs/edai-1/processing_jobs.md#full-scale-ingest) |
| Schema and dedup experiments | Three pairs each: declared-schema median 0.927→0.764 s; aggregate dedup candidate 0.603 s versus window 0.581 s, so the window method is retained | [Controlled comparisons](docs/edai-1/processing_jobs.md#batch-corrections) |
| Airflow skew benchmark | Five successful tasks; 17.28M Silver rows; median 14.258→9.170 s with identical 4,320-row results | [Native Airflow run](docs/edai-1/skew_benchmark.md#airflow-benchmark-execution) |
| ML feature dataset | Separate 60-day source; 3,456,000 snapshots and 3,132,184 eligible training rows | [Feature dataset](docs/edai-1/medium_feature_dataset.md) |
| Streaming stores | 5,760 finalized windows / 57,600 unique readings across 20 machines, with replay checks | [Window cohort](docs/edai-1/window_cohort.md) |
| Gold warehouse indexing | Local DuckDB, eight paired batches of 64 exact lookups: 143.994 → 45.275 ms median; 2,883,584 additional bytes | [Warehouse experiment](docs/edai-1/data_storage.md#warehouse-index-experiment) |
| Native feature serving | Model 6: 195 valid predictions and five stopped-machine HTTP 409 responses across 200 machines | [Serving record](docs/edai-1/final_handover.md) |
| Routed LLM benchmark | Official llm-d-benchmark with the same 7B backend, 6 sequential and 11 concurrent completions in 30 s | [Native benchmark](docs/edai-2/llm_inference_platform.md#upstream-llm-d-benchmark-run) |
| Agentgateway | Default route preserved through Jenkins 18; both child A2A paths joined to gateway logs and worker Pod identities | [Native gateway](docs/edai-2/global_model_config.md#native-agentgateway-integration) |
| Elastic capacity | Both MCP services and all three worker pools observed at 2 → 4 → 2; actual new-Pod tool work and twelve active worker identities | [MCP](docs/edai-2/mcp_tools.md#native-mcp-autoscale), [workers](docs/edai-2/agent_scaling.md#latest-native-worker-run) |
| Agent observability | Real failed/completed A2A calls with automatic ERROR/OK and complete 10-/44-span named-service graphs | [Jaeger evidence](docs/edai-2/observability.md#compare-failed-and-completed-coordinator-tasks) |
| Prompt delivery | MLflow Prompt Registry v1 for all three roles, hash admission, OPA and real coordinator verification in build 14 | [CI/CD](docs/edai-2/ci_agents.md#latest-retained-gateway-release) |
| RAG governance | Six actual Airflow tasks, 18 chunks, ten persisted checks, DataHub lineage and invalid-source rejection | [RAG pipeline](docs/edai-2/rag_orchestration.md) |
| Complete agent answer | One accepted post-restart parent, two independently checked child tasks, four successful MCP tools and exact model-6 arithmetic | [Runtime validation](docs/edai-2/agent_runtime_validation.md) |
| MCP API quality | 132 passing cases; 98.03% statement / 90.74% branch coverage; strict mutation result 681/850, with source-bound reuse stated explicitly | [Testing report](docs/edai-2/mcp_quality_evidence.md) |
| Monitoring/load | 128 exact CRI/Loki events, 24 matching panel queries and 108 streamed requests across 1/4-client profiles | [Native monitoring](docs/edai-2/llm_monitoring_demo.md) |

The 100 GB workload ends at Bronze/Silver; it is not a claim of training on 100 GB. Scores are uncalibrated and based on simulated equipment. Original failures, wrong answers, mixed startup timings and SDK trace warnings remain in the linked reports. The [117-row requirement map](docs/edai-1/implementation_map.md) connects code and scoped evidence without awarding a grade.

## Quick start

Run from the repository root with **Python 3.12/3.13** and **Git**. Examples use PowerShell 7.

### Generate a small dataset

This uses Python's standard library and needs no Docker or cloud VM:

```powershell
$run = "local-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
python -S -m generator generate --config generator/configs/dev.json --output "data/source/$run"
python -S -m generator inspect --source "data/source/$run" --report "outputs/$run/quality.json"
```

The development configuration simulates 20 machines over 48 hours. See the [generator guide](generator/README.md) for inputs, outputs and validation boundaries.

### Verify the recording

```powershell
$tag = Get-Date -Format 'yyyyMMdd-HHmmss'
python -B -S scripts/audit-final-coursework.py --output "outputs/audit-$tag"
```

The audit restores the byte-pinned historical receipt bundle into ignored local files, validates both original rubric views and runs **60 offline artifact checks**. It writes the result and original verifier consoles under `outputs/`. No service starts and no model request is sent. To run an individual verifier first, restore once with `python -B -S scripts/unpack-evidence.py`. See [evidence storage](docs/edai-1/evidence_guide.md).

### Use the live lab

The [operations runbook](docs/edai-1/operations.md) covers retained CPU/GPU recovery, genuine feature preparation, private access and ML/agent commands. Live requests require that private runtime and an unexpired one-hour feature lease. A Git clone contains no bulk datasets, credentials, service state or LLM weights.

## Repository structure

```text
generator/             Synthetic pump histories and delivery faults
pipelines/             Spark/Flink jobs, temporal contracts and validation
airflow/dags/          Data, feature-materialization and drift DAGs
feature_store/         Feast integration, offline/online delivery and replay
ml/                    Training, model/data versioning and drift logic
services/              Feature, predictor, drift, A/B and MCP APIs
agents/                Agent policy, release contracts and answer acceptance
rag/                   Reference ingestion, retrieval and corpus governance
knowledge/             Authored simulator reference documents
llm/                   Inference evaluation and bounded comparisons
llm_observability/     Request facts, traces, metrics and policy controls
infra/                 Images, Helm, Terraform, Ansible, Jenkins and security
notebooks/             Maintained training and agent notebooks
scripts/               Stable operation and verification entry points
tests/                 Unit, integration, property and load tests
docs/                  Technical reports, runbooks and review indexes
  evidence/            Browsable captures, manifests and selected results
  evidence-archive/    Checked archive of original machine-readable receipts
references/            Original specifications and validation criteria
```

Start with a package's README or the [command index](scripts/README.md). Runtime libraries, command entry points and captured evidence have separate roles. `data/`, `outputs/`, environments and credentials stay outside Git.

## Documentation

| Chapter | Code and evidence in one place |
|---|---|
| [Data processing](docs/edai-1/processing_jobs.md) | Batch and stream jobs, actual Flink window code, Airflow, and before/after results |
| [Data storage](docs/edai-1/data_storage.md) | Medallion table model, zone responsibilities, grains and keys; Parquet, Gold SQL, indexing, compaction and Feast |
| [LLM inference](docs/edai-2/llm_inference_platform.md) | Deployment steps, recorded model requests and latency measurements |
| [Agentgateway and ModelConfig](docs/edai-2/global_model_config.md) | Default model/MCP route, parent and child A2A, configuration and native acceptance |
| [MCP tools](docs/edai-2/mcp_tools.md) | Async handlers, Helm recovery and per-Pod autoscale proof |
| [Substrate comparison](docs/edai-2/agent_warmup.md) | Matched ordinary-Agent control and original startup measurements |
| [Worker scaling](docs/edai-2/agent_scaling.md) | Healthy demand, actual Pod assignments and automatic scale-in |

The [documentation index](docs/README.md) includes the remaining topics and historical experiment records. The [ML tracker](docs/edai-1/rubric_tracker.md), [LLM tracker](docs/edai-2/rubric_tracker.md) and [final audit](docs/edai-1/final_audit.md) retain the original requirement and evidence scopes.

Maintained prose and code descriptions use English. Original workbooks, native logs and screenshots retain their capture language and bytes. Presentation references: [RecSys-MLops](https://github.com/itsmekhoathekid/RecSys-MLops/tree/6bd6e99f481f2a8d5886d6f0e8356540dc20e135) and [Card Fraud Platform](https://github.com/cvinhnguyen/card-fraud-platform/tree/cc34515304815774a5dad6f796683b46c27f93ac). The diagrams and measurements describe this repository's implementation and the limits of its recorded experiments.

## Contributors

- [An Le](https://github.com/anle0429) — project owner.
- [OpenAI Codex](https://developers.openai.com/codex/) — AI assistance with documentation and architecture diagrams.
