# Architecture

This document describes the IoT lab implemented in this repository: the prediction request, the data and training jobs behind it, and the controls around deployment. The [README](../README.md#system-diagram) provides the platform overview; the [demo guide](demo-walkthrough.md) provides the operating commands.

The selected coursework scope includes both final ML and final LLM/agents. This page describes recorded Data/ML behavior; the [LLM workstream](llm-agent-plan.md) is selected but not implemented.

## Scope and runtime

The lab runs on one Google Cloud VM with 8 vCPU and 64 GiB RAM. K3s hosts Kubernetes workloads; Docker containers and host services provide the remaining components. Shared data and scratch volumes hold datasets, model artifacts and service state. Administration uses loopback ports and IAP.

Three workloads have different boundaries:

| Workload | Recorded scope | Evidence |
|---|---|---|
| Large-source processing | 102,447,847,856 bytes of uncompressed JSONL through Spark Bronze/Silver | [Full-scale ingest](full-scale-ingest.md) |
| Feature engineering and ML | A separate 60-day dataset, 3,456,000 snapshots and 3,132,184 training rows | [Feature dataset](medium-feature-dataset.md), [training protocol](ml-evaluation-protocol.md) |
| Streaming store checks | Retained Kafka/Flink output: 5,760 finalized windows across 20 machines; separate small CI/Airflow fixtures | [Window cohort](window-cohort.md), [stream CI](ci-stream-push.md) |

The 100 GB result does not cover feature engineering or model training at that scale. Diagrams summarize recorded workflows; they do not assert that all jobs run concurrently or that endpoints are currently ready.

![Data and ML workflow overview](images/platform-overview.png)

## Inference request

The API accepts a machine ID, retrieves its features through Feast, checks whether they can support the prediction, and calls the pinned KServe model. Failed or maintenance-state machines can still have monitoring features, but the one-hour failure target is undefined for them.

```mermaid
flowchart LR
    Client["Client"] -->|U1 HTTPS machine request| Gateway["NGINX Ingress Controller"]
    Gateway -->|U2 Authenticated and rate-limited request| API["Feature API Deployment"]
    API -->|U3 Machine feature lookup| Redis["Redis API Deployment"]
    Redis -->|U4 Values and native timestamps| API
    API -->|U5 Validated eight-feature vector| Predictor["KServe predictor Deployment"]
    Predictor -->|U6 Score and model identity| API
    API -->|U7 Prediction and feature trace| Client
```

Feast SDK runs inside the API process. The [runtime](../services/feature_api/runtime.py) validates the schema, event time, arrival context, protocol and one-hour demo lease. Before returning a prediction, it also checks the model version, run, data version and threshold against the selected pin.

### Follow the request in code

| Step | Entry point | What to inspect |
|---|---|---|
| HTTP routing | [feature API app](../services/feature_api/app.py) | Machine-ID validation, readiness, async dispatch and HTTP error mapping |
| Feature eligibility | [FeatureRuntime](../services/feature_api/runtime.py) | `features()` checks both clocks and native feature timestamps; `predict()` checks operating state |
| Store and model calls | [FeastKServeBackend](../services/feature_api/runtime.py) | Read the sealed serving state, use Feast online retrieval and call the fixed KServe endpoint |
| Model identity | [binding](../services/predictor/binding.py) | Validate the model pin and required lineage fields |
| Numeric inference | [PumpPredictor](../services/predictor/runtime.py) | Validate the portable model, compute a score and apply the recorded threshold |
| Independent HTTP check | [demo CLI](../scripts/demo-feature-api.py) | Recompute the score, compare repeated responses and check rejected requests |

With a valid runtime and an unexpired lease, the demonstration covers:

| Request | Expected result | Reason |
|---|---|---|
| `GET /health/live` | 200 | The process is running |
| `GET /health/ready` | 200 | A real online lookup passes the eligibility checks |
| `POST /v1/machines/P-001:predict` | 200 | An operating machine has eligible features |
| `POST /v1/machines/P-006:predict` | 409 | The machine is stopped |
| `GET /v1/machines/P-9999/features` | 404 | The ID is valid but absent |
| `GET /v1/machines/invalid/features` | 422 | The ID does not match the API contract |
| Feature lookup with an expired lease or invalid upstream data | 503 | Inference cannot safely proceed |

The response retains feature timestamps, source/protocol hashes and model provenance. The risk score is not a calibrated real-world failure probability. See [API validation](api-validation.md), [serving CI](ci-serving.md) and the [after-restart checks](final-audit.md).

## Batch data and feature stores

Airflow coordinates DP1 ingestion, DP2 cleaning/aggregation and DP3 feature/label preparation. Each stage validates outputs and publishes metadata before the next stage proceeds. A publisher job sends schemas, lineage and assertions to DataHub.

Spark writes persisted Parquet datasets; downstream jobs read those files from shared volumes. File transfers in the overview diagram are not direct network calls between Spark and the consumer jobs. The full-source Bronze/Silver run and the smaller Airflow DP1-DP3 runs have separate receipts.

The two feature views serve different purposes:

| View | Contents | Use |
|---|---|---|
| `machine_rolling_10m` | Eight inputs computed over the past ten minutes | Historical training and online failure-risk inference |
| `machine_tumbling_final_10m` | Four aggregates from finalized non-overlapping Flink windows | Streaming monitoring and replay checks |

Batch/stream Redis and API Redis are configured separately. Materialization and serving-preparation jobs populate the appropriate store. Finalized stream aggregates are not substituted for the predictor's eight inputs.

There are also separate time limits: a feature's event-time age, Redis key expiry and the demo's wall-clock lease. A Redis key can exist while its event-time data is too old. Offline Parquet history does not expire with the Redis key. See [Feast](feast-feature-store.md), [materialization](feast-materialization.md) and [TTL policy](feature-jobs-and-ttl.md).

## Streaming writes and replay

Kafka receives telemetry replayed in arrival order. Flink performs event-time aggregation and retains finalized windows. Separate offline and online jobs consume that retained output:

- The offline job writes validated history to Parquet and registers the feature source.
- The online job reads the verified windows and pushes the latest eligible monitoring vectors through Feast.
- Both paths retain state so an acknowledged replay does not increase counts or renew Redis keys.

These are bounded replay checks of real retained Kafka/Flink output. The native 5,760-window run is separate from the three-window CI/Airflow demonstration. They do not establish exactly-once behavior under every crash or a new continuous live source. See [stream pipeline](day3-streaming.md), [CI deployment](ci-stream-push.md) and [full window cohort](window-cohort.md).

## Training and model lifecycle

The notebook and Kubernetes pipeline execute five ML steps. The pipeline is defined in [kfp_notebook.py](../ml/kfp_notebook.py); the executed notebook and parity checks are linked from [training-notebook.md](training-notebook.md).

| Step | Responsibility |
|---|---|
| 1. Load cohort | Read labels separately and retrieve historical features through Feast |
| 2. Split by time | Apply the fixed train/validation/test boundaries and label-window guards |
| 3. Train | Fit using two Spark executor Pods on the same VM; select using validation data |
| 4. Validate saved model | Reload the saved weights and reproduce validation behavior |
| 5. Evaluate holdout | Evaluate the selected model under the unchanged protocol |

Dataset changes are stored incrementally and linked to MLflow runs and model versions. A trained candidate is distinct from a serving release: the recorded retraining run created model 7 as a candidate, while the demonstrated serving pin remains model 6.

Jenkins serving CI retrieves the selected model bundle, runs gates, builds images and deploys with Helm. The recorded A/B experiment compared versions1 and6; controlled rollback restored the previous version before returning to6. This is separate from model 7 training. See [distributed training](distributed-training.md), [automatic versioning](automatic-ml-versioning.md), [serving CI](ci-serving.md) and [A/B deployment](ci-ab-deployment.md).

## Monitoring and retraining

Offline drift monitoring reads a bounded cohort through Feast, compares eight feature distributions with a fixed training reference using PSI, and publishes metrics through Pushgateway. Prometheus scrapes these metrics and Grafana displays them alongside the age of the last successful comparison.

```mermaid
flowchart LR
    Airflow["Airflow scheduler"] -->|M1 Submit comparison window| Drift["Offline drift job"]
    Drift -->|M2 PSI and freshness gauges| Push["Pushgateway"]
    Push -->|M3 Scraped metrics| Prom["Prometheus"]
    Prom -->|M4 PromQL results| Grafana["Grafana"]
    Airflow -->|R1 Report path and retraining policy| Controller["Retrain controller container"]
    Controller -->|R2 Admitted package and run parameters| KFP["Kubeflow Pipelines API"]
    KFP -->|R3 Backend run status| Controller
```

The comparison uses a fixed model 3 training reference. Its PSI values are not accuracy measurements for the served model 6. The recorded retraining test was a manual probe with `drift=false`; it demonstrated a real KFP run and the admission/replay/cooldown controls. It did not establish a naturally occurring drift alarm or automatic promotion.

The deployment also has API metrics and OTLP traces, host/Pod logs, and a separate Knative Eventing-to-KServe drift receiver. Those have their own workloads and evidence. MMD is an additional offline diagnostic for joint sensor-distribution changes. See [offline drift](offline-drift-monitoring.md), [retraining](drift-retraining.md), [API observability](api-observability.md), [logs](log-discovery.md), [Eventing](drift-eventing.md) and [MMD](joint-cohort.md).

## Deployment and security

| Runtime | Components and state | Configuration or report |
|---|---|---|
| Google Cloud VM | One 8-vCPU/64-GiB host; separate boot, data and scratch disks | [Terraform](../infra/gcp/vm/), [IaC verification](iac-verification.md) |
| K3s | APIs, KServe, Kubeflow Pipelines, ingress and selected security/Eventing workloads | [Helm charts](../infra/charts/), [KServe config](../infra/kserve/pump-failure.yaml) |
| Docker | Spark jobs, Kafka/Flink, Airflow, DataHub, MLflow and Jenkins | [Orchestration Compose](../infra/orchestration.compose.yaml), [MLflow Compose](../infra/mlflow.compose.yaml) |
| Host services | Prometheus, Grafana, Loki, Alloy, Pushgateway, exporter and Vault processes | [Host metrics](host-monitoring.md), [recovery](demo-walkthrough.md) |

The gateway provides authentication, rate limiting and private-CA HTTPS. Vault supplies scoped credentials to two real consumers: Jenkins and the gateway credential-sync job. Mesh authorization was tested with workload identities in a separate namespace. OPA checks a deployment plan in CI; it is a policy gate, not a cluster admission webhook.

Database, artifact and registry state stays on VM volumes. A Git clone does not reproduce that state or credentials. The lab has not been validated as a multi-host HA deployment. See [gateway](private-gateway.md), [Vault](vault.md), [mesh](private-mesh.md) and [OPA](deployment-policy.md).

## Review and reproduce

1. Follow [one inference request](#inference-request) from the route to the model and inspect its trace.
2. Use the [demo guide](demo-walkthrough.md) for actual service access, feature refresh and expected HTTP results.
3. Open the [final audit](final-audit.md) and [rubric matrix](final-audit-matrix.md) to locate the relevant code, reports and screenshots.
4. Run `python -S scripts/audit-final-coursework.py --output outputs/<new-run>` from the repository root to verify recorded artifacts without starting the platform.

Earlier design sketches and model versions are retained in [Git history](https://github.com/anle0429/iot-maintenance-platform/blob/1c9d1932ea652f09b1b2b77caa001b082a2b4be7/docs/architecture.md). They describe earlier milestones, not the current lab design.

The supplied rubrics govern scope. [Card Fraud Platform](https://github.com/cvinhnguyen/card-fraud-platform) is a presentation reference; the illustrations and code walkthrough describe this repository's IoT implementation.

## Selected LLM workstream

![Staged LLM architecture: verified inference and planned agents](images/platform-llm-plan.png)

The llm-d router and CPU backend have [native inference evidence](llm-inference.md); the remaining RAG/agent components are planned or in progress. See the [learning guide](llm-learning-guide.md), [workstream](llm-agent-plan.md), [source rubric](rubric-source.md) and [LLM tracker](final-llm-rubric-tracker.md). Shared ML APIs are integration points. The drawing itself is not runtime evidence.
