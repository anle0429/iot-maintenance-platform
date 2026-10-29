# IoT Maintenance Platform

Sensor data pipelines and failure prediction for industrial pumps.

This project follows pump telemetry from ingestion and validation through feature engineering, model training and API inference. Data and model versions, deployment checks and monitoring records make it possible to trace a prediction back to its inputs.

Built as an individual **Engineering for Data & AI — EDAI K11** coursework project. All equipment records, sensor readings and failure events are simulated.

[Demo guide](docs/demo-walkthrough.md) · [Architecture details](docs/architecture.md) · [Verification report](docs/final-audit.md)

## Table of contents

- [Business domain](#business-domain)
- [Architecture](#architecture)
  - [Data platform](#data-platform) · [ML platform](#ml-platform)
  - [System diagram](#system-diagram) · [Deployment](#deployment)
- [Recorded results](#recorded-results)
- [Quick start](#quick-start)
- [Repository structure](#repository-structure)
- [Documentation](#documentation)

## Business domain

Industrial pumps can show signs of wear before they stop working. Temperature, vibration, motor current and rotational speed provide a history of that process. Maintenance teams need to inspect the latest readings and identify operating machines that may fail soon.

The prediction target is `failure_in_next_1h`: a failure starts in **(t, t + 1 hour]**, for a machine operating at time t. Failed or maintenance-state machines are excluded from this target. Records without enough future observation are not treated as negative examples.

| Data | Purpose | Issues handled |
|---|---|---|
| Machine metadata | Equipment identity, factory and effective-dated attributes | Historical changes and uneven factory sizes |
| Historical telemetry | Temperature, vibration, current and RPM at each observation | Duplicates, schema evolution and skew |
| Streaming telemetry | Arrival-ordered replay of readings through Kafka | Retry duplicates, late events and out-of-order arrival |
| Failure and maintenance events | Failure labels and operating-state history | Downtime, observation boundaries and information availability |

One telemetry row represents one machine at one event time. The platform retains both event and arrival timestamps: a feature must have been available when the prediction was made. See the [data contract](docs/data-contract.md) and [evaluation protocol](docs/ml-evaluation-protocol.md) for the labeling, temporal split and leakage checks.

## Architecture

The data platform produces validated historical features and finalized streaming windows. The ML platform uses those outputs for training and inference. Batch and serving jobs use Feast SDK inside their own processes; offline Parquet files and online Redis instances hold the feature data.

### Data platform

| Component | Responsibility |
|---|---|
| Generator | Simulate pump wear, operating load, maintenance and data-delivery faults using a fixed seed |
| Spark | Run DP1 ingestion, DP2 cleaning and aggregation, and DP3 feature/label preparation |
| Kafka and Flink | Replay telemetry, apply event-time windows and deduplication, and retain finalized windows |
| Airflow | Schedule data pipelines, validation, materialization and drift checks |
| DataHub | Publish schemas, lineage and validation assertions |

Each pipeline validates its outputs before publishing them. Source generation, large-scale Bronze/Silver processing and feature preparation have separate run reports and resource budgets.

### ML platform

| Component | Responsibility |
|---|---|
| Feast SDK and feature jobs | Retrieve historical features, materialize online data and check replay behavior |
| Notebook and Kubeflow Pipelines | Execute the same five ML steps, including training across two Spark executor Pods |
| MLflow | Track training runs, incremental data versions, model artifacts and registry versions |
| FastAPI and KServe | Check feature eligibility, run the selected model and return a score with model and feature provenance |
| Drift jobs and API | Compare input distributions with a fixed reference; control retraining requests with replay and cooldown guards |
| Jenkins and Helm | Test, build and deploy releases, with A/B checks and rollback |

Prometheus, Grafana, Alloy, Loki and Jaeger cover metrics, logs and traces. Security checks include gateway authentication and rate limits, Vault AppRole access, an isolated service-mesh test and an OPA deployment-policy gate.

### System diagram

Boxes represent deployable services or jobs. Arrow labels identify the data or control request and number each flow: **U** inference, **D** batch data, **S** streaming, **T** training, **C** CI/CD and **O** observability. File transfers use shared volumes; an arrow labeled Parquet does not represent a direct network call between jobs.

```mermaid
flowchart TB
    User["User"] -->|U1 HTTPS request with machine ID| Gateway["NGINX Ingress Controller"]
    Gateway -->|U2 Authenticated and rate-limited request| API["Feature API Deployment"]
    API -->|U3 Feature lookup| Redis["Redis API Deployment"]
    Redis -->|U4 Features and timestamps| API
    API -->|U5 Validated input vector| Predictor["KServe predictor Deployment"]
    Predictor -->|U6 Risk score and model identity| API
    API -->|U7 Prediction and feature provenance| User

    Generator["Generator job"] -->|D1 Historical telemetry files| Spark["Spark batch job"]
    Airflow["Airflow scheduler"] -->|D2 Submit DP1-DP3| Spark
    Spark -->|D3 Parquet features via shared volume| Materialize["Materialization job"]
    Materialize -->|D4 Online features| BatchRedis["Redis batch-stream container"]
    Spark -->|D5 Validation reports| Publisher["Metadata publisher job"]
    Publisher -->|D6 Schemas, lineage and assertions| DataHub["DataHub GMS"]
    Spark -->|D7 Serving feature bundle via shared volume| Prepare["Serving preparation job"]
    Prepare -->|D8 Online features and context| Redis

    Generator -->|S1 Telemetry replay| Kafka["Kafka broker"]
    Kafka -->|S2 Events in arrival order| Flink["Flink job"]
    Flink -->|S3 Retained finalized windows| Offline["Stream offline job"]
    Offline -->|S4 Verified windows via shared volume| Online["Stream online job"]
    Online -->|S5 Latest monitoring features| BatchRedis

    Jenkins["Jenkins controller"] -->|C1 Submit pipeline after test and build| KFP["Kubeflow Pipelines API"]
    Spark -->|T1 Offline Parquet features through Feast SDK| Training["Training task Pod"]
    KFP -->|T2 Schedule ML steps| Training
    Training -->|T3 Distributed tasks| Executor["Spark executor Pod x2"]
    Executor -->|T4 Task results| Training
    Training -->|T5 Data versions and model artifacts| MLflow["MLflow tracking server"]
    MLflow -->|C2 Selected model bundle| Jenkins
    Jenkins -->|C3 Image and Helm release| Predictor
    Vault["Vault Raft server"] -->|C4 CI credentials through AppRole| Jenkins

    API -->|O1 Metrics| Prometheus["Prometheus"]
    Prometheus -->|O2 PromQL results| Grafana["Grafana"]
    Alloy["Alloy collector"] -->|O3 Host and Pod logs| Loki["Loki"]
    Loki -->|O4 LogQL results| Grafana
    API -->|O5 OTLP spans| Jaeger["Jaeger"]
```

Batch/stream Redis and API Redis are configured separately. The two executor Pods run on the same physical VM. The diagram summarizes flows verified in separate runs; it does not imply that every job runs concurrently. Streaming store checks replay retained Kafka/Flink windows. The [architecture documentation](docs/architecture.md) includes a [request-to-code walkthrough](docs/architecture.md#follow-the-request-in-code) and a separate [monitoring/retraining view](docs/architecture.md#monitoring-and-retraining).

### Deployment

The lab uses one Google Cloud VM with **8 vCPU and 64 GiB RAM**, separate data and scratch disks, and private administration through IAP. Terraform provisions the infrastructure; Ansible configures the base environment.

| Runtime | Workloads |
|---|---|
| K3s | Feature and drift APIs, KServe, Kubeflow Pipelines, gateway and selected monitoring/security workloads |
| Docker containers | Spark jobs, Kafka/Flink, Airflow, DataHub, MLflow and Jenkins |
| Host services | Prometheus, Grafana, Loki, Alloy and Vault |
| Developer PC | Editing, local generator runs, offline verification and IAP access to the VM |

The gateway uses private lab domains and a private CA. Service-mesh authorization was tested in a separate namespace. Deployment details and recovery procedures are in the [infrastructure report](docs/iac-verification.md) and [demo guide](docs/demo-walkthrough.md).

## Recorded results

| Area | Result | Report |
|---|---|---|
| Large-source processing | 102,447,847,856 bytes of uncompressed JSONL; 316.8 million unique Silver rows; 13 Spark checks passed | [Full-scale ingest](docs/full-scale-ingest.md) |
| Feature dataset | 3,456,000 snapshots over 60 days; 3,132,184 training rows after protocol filters | [Feature dataset](docs/medium-feature-dataset.md) |
| Streaming stores | 5,760 windows and 57,600 readings across 20 machines, written and replayed through offline/online stores | [Window cohort](docs/window-cohort.md) |
| Inference | Model version 6 demo: 195 valid predictions and 5 stopped-machine HTTP 409 responses across 200 machines | [Serving handover](docs/final-handover.md) |
| PC verification | 748 tests and 314 subtests passed; 7 documented skips; 26 evidence verifiers passed from an independent clean clone | [Final audit](docs/final-audit.md) |

The 100 GB run covers source ingestion through Bronze/Silver. Feature engineering and ML use the separate 60-day dataset. API coverage, mutation scores and load-test measurements are recorded with their workloads in [serving CI](docs/ci-serving.md), [API validation](docs/api-validation.md) and [load testing](docs/observed-api-load.md).

These are synthetic-data lab results. The model's score is not a calibrated real-world failure probability, and the deployment has not been tested for high availability across multiple machines. The [final rubric matrix](docs/final-audit-matrix.md) links each requirement to its implementation and evidence.

## Quick start

Run these commands from the repository root with **Python 3.12/3.13**. Examples use **PowerShell 7**.

### Generate a small dataset

This step uses only Python's standard library and does not require Docker or a cloud VM.

```powershell
$run = "local-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
python -S -m generator generate --config generator/configs/dev.json --output "data/source/$run"
python -S -m generator inspect --source "data/source/$run" --report "outputs/$run/quality.json"
```

The default configuration simulates 20 machines over 48 hours. Use a new run name each time to preserve earlier datasets and reports. The [generator guide](docs/day1-generator.md) explains the seed, machine count and fault settings.

### Verify the recorded evidence

This step requires Python and Git, with no running platform services.

```powershell
$tag = Get-Date -Format 'yyyyMMdd-HHmmss'
python -S scripts/audit-final-coursework.py --output "outputs/audit-$tag"
```

The audit checks the original rubric, payload hashes and 26 offline verifiers. It writes `verification.json` and the verifier consoles to the output directory. It checks recorded results rather than rerunning the pipelines.

### Call the lab API

With the VM ready and the IAP tunnel open:

```powershell
$tag = Get-Date -Format 'yyyyMMdd-HHmmss'
python -S scripts/demo-feature-api.py --base-url http://127.0.0.1:18082 --output "outputs/demo-$tag"
```

Open [Swagger UI](http://127.0.0.1:18082/docs) and try `P-001` and `P-006`. The demo checks prediction arithmetic, repeat requests and HTTP 409, 404 and 422 responses. Features expire; an expired cache must be refreshed through materialization before inference resumes.

The [demo guide](docs/demo-walkthrough.md) covers VM recovery, feature refresh, port forwarding and a 10-minute presentation. A Git clone contains code, configuration templates and documentation. Large datasets, credentials, Python environments and service state remain local to each machine.

## Repository structure

```text
generator/             Pump simulation, telemetry, maintenance and delivery faults
pipelines/             Batch/stream processing, aggregation and temporal checks
feature_store/         Feast integration, materialization and store replay
airflow/dags/          Data, materialization and drift-monitoring DAGs
ml/                    Training, versioning, serving and drift logic
notebooks/             Training and analysis notebooks
services/              Feature, predictor, drift and A/B APIs
models/                Portable models and version metadata used by checks
infra/                 Docker, Helm, Terraform, Ansible, CI and security config
scripts/               Run, operation and verification commands
tests/                 Unit, integration, property-based and load tests
docs/                  Design, runbooks, measurements and evidence
references/coursework/ Original coursework specification and rubric
```

`data/`, `outputs/`, local Python environments and credentials are excluded from Git.

## Documentation

To review the implementation, start with [one inference request](docs/architecture.md#inference-request), follow its code entry points, then use the [demo guide](docs/demo-walkthrough.md) and [rubric matrix](docs/final-audit-matrix.md) to locate the corresponding runtime evidence.

| Topic | Start here |
|---|---|
| Data and labeling | [Data contract](docs/data-contract.md) · [Evaluation protocol](docs/ml-evaluation-protocol.md) |
| Data engineering | [Spark at 100 GB](docs/full-scale-ingest.md) · [Airflow and DataHub](docs/orchestration.md) · [Kafka/Flink](docs/day3-streaming.md) |
| Feature store | [Feast](docs/feast-feature-store.md) · [Materialization and TTL](docs/feature-jobs-and-ttl.md) |
| ML lifecycle | [Notebook](docs/training-notebook.md) · [Distributed training](docs/distributed-training.md) · [Incremental versioning](docs/automatic-ml-versioning.md) · [Five key classes](docs/ml-class-design.md) |
| Serving and CI/CD | [Feature API](docs/feature-api.md) · [Serving CI](docs/ci-serving.md) · [A/B deployment](docs/ci-ab-deployment.md) |
| Observability | [Metrics and traces](docs/api-observability.md) · [Logs](docs/log-discovery.md) · [Drift and retraining](docs/drift-retraining.md) |
| Infrastructure and security | [Terraform/Ansible](docs/iac-verification.md) · [Gateway](docs/private-gateway.md) · [Vault](docs/vault.md) · [Service mesh](docs/private-mesh.md) |
| Extensions | [MMD for joint sensor drift](docs/joint-cohort.md) · [OPA deployment-policy checks](docs/deployment-policy.md) |
| Coursework review | [Demo walkthrough](docs/demo-walkthrough.md) · [Final audit](docs/final-audit.md) · [Rubric matrix](docs/final-audit-matrix.md) |

Detailed runbooks are currently in Vietnamese. Runtime versions, configurations and screenshots are recorded in the relevant reports. The original specification and workbook are under [`references/coursework/`](references/coursework/).

Documentation references: [Card Fraud Platform](https://github.com/cvinhnguyen/card-fraud-platform/tree/cc34515304815774a5dad6f796683b46c27f93ac) for README organization, and [RecSys-MLops](https://github.com/itsmekhoathekid/RecSys-MLops) for separating architecture views and connecting them to code and evidence.
