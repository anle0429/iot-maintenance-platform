# Architecture views

Five complementary views describe the recorded lab. The overview introduces the data and model lifecycle; the other views expand a request, deployment boundaries, operational signals and agent execution. They are authored illustrations, not runtime screenshots or a claim that every job runs concurrently.

| View | What it explains | Files |
|---|---|---|
| 01 · Platform overview | Separate large-source, ML and stream cohorts; Airflow orchestration and DataHub metadata | [PNG](../images/platform-overview.png) · [SVG](../images/platform-overview.svg) |
| 02 · Prediction request | Authenticated request, Feast lookup, eligibility gates and pinned KServe prediction | [PNG](../images/platform-serving.png) · [SVG](../images/platform-serving.svg) |
| 03 · Deployment topology | Forty selected workload/client resources, grouped by host and runtime | [PNG](../images/platform-deployment.png) · [SVG](../images/platform-deployment.svg) |
| 04 · Observability and retraining | Metrics, logs, traces, job gauges and guarded candidate training | [PNG](../images/platform-monitoring.png) · [SVG](../images/platform-monitoring.svg) |
| 05 · RAG and agents | Knowledge preparation, coordinator delegation, separate MCP/API paths and shared GPU generation | [PNG](../images/platform-llm-plan.png) · [SVG](../images/platform-llm-plan.svg) |

## Reading the drawings

The heading of each card identifies its runtime/resource type. Colored frames group workflows or runtimes; a frame is not another service. SDKs run inside their consuming job or API. A WorkerPool can own several Pods, and the observer/turn adapter is one Deployment with two containers.

| Accent | Meaning |
|---|---|
| Teal | Data preparation and feature jobs |
| Blue | APIs, messaging and agent services |
| Purple | ML tasks, inference and model routing |
| Amber | Persistent feature, database or snapshot state |
| Slate | Scheduling, telemetry and operational controls |
| Rose | Authenticated gateway boundary |

Arrows show the direction of data, results, artifacts or task submission. They do not universally denote a direct network call: Spark/Feast handoffs use persisted files, and the pipeline's MLflow artifacts are produced by task code. In the overview, dotted arrows identify orchestration and metadata publication. Crossings without a node do not join flows. Numbered labels are local to each view.

| View | Handoff IDs |
|---|---|
| Overview | D1–D2: large-source ingestion; F1–F3: separate ML source/materialization; C1/G1: control and metadata; T0–T2/C2–C3: training/release; S0–S4: stream replay/writes, with S3 carrying verified offline history and its release pin |
| Prediction | U1–U2: incoming request; U3–U4: online lookup/values; U5–U6: model request/result; U7: logical response through the gateway |
| Deployment | U1–U2: browser, loopback viewer and IAP-backed access; G1–G2: router/observer/private GPU request; G3: token stream |
| Observability | H1/M1/M3: metric sources; L1–L3: logs/query results; M2: metric queries; O1–O2: traces/private viewer; R1–R4: drift gauges and admitted training |
| RAG/agents | K0–K2: reference preparation/retrieval; A1–A4: controller/delegation; M1–M4: MCP/API calls; G1–G5: shared generation and return stream |

The 102.45 GB result covers Bronze/Silver, while feature engineering and ML use a separate 60-day cohort. The serving pin remains model 6; model 7 is a candidate. Stream aggregates have a different schema. Additional worker replicas and the separate GPU host do not establish host-level HA. See [architecture](../architecture.md) and the [evidence map](../final-audit-matrix.md) for implementation and measurement boundaries.

## Implementation boundaries

These source checks explain the choices in the drawings. They distinguish logical handoffs from physical routing and current deployment from earlier experiments.

| Boundary | Source check | Reading consequence |
|---|---|---|
| Offline and online stream jobs | [OnlineStage](../../feature_store/staged_push.py) validates the completed offline receipt, reads its Parquet history and selects the latest vector | S3 is a sealed data/release dependency, not a direct RPC between two running services. Replaying acknowledged windows does not renew their TTL. |
| One cluster, two physical nodes | [GPU chart](../../infra/charts/iot-gpu-model/templates/model.yaml) selects the GPU node; the [deployment operator](../../scripts/deploy-gpu-backend.py) validates that worker through the existing control cluster | The GPU frame is a worker in the same K3s cluster as the CPU server. The earlier standalone Docker benchmark is a separate experiment. |
| Browser access and TLS | The [private viewer](../../scripts/view-agent-gateway.py) listens on loopback HTTP and verifies the gateway's CA and hostname on its upstream HTTPS connection | U1–U2 summarize the IAP/viewer access path. The browser's loopback HTTP address does not describe the gateway's upstream transport. |
| Prediction response | The [Feature API](../../services/feature_api/runtime.py) uses Feast and the pinned predictor; the gateway remains the external entry point | U7 is the logical return through the gateway. It does not imply direct public access to the API or predictor. |
| Delegation and answer acceptance | The [coordinator checker](../../agents/delegation_acceptance.py) compares the parent answer, actual child sessions and their MCP results | A1–A4 show task dispatch. Specialist answers return to the coordinator and then through the controller/UI; a completed task alone is insufficient evidence of a correct answer. |

The monitoring view shows signal and query-result direction. For example, a Prometheus-to-Grafana arrow represents returned query data; it does not imply that Prometheus initiates a Grafana request. The same convention applies to the protected trace-view response.

## Vector source and rendering

The SVG files retain editable text, paths and semantic groups (`node-*`, `edge-*`, `boundary-*`). They contain no external images or font downloads. PNG files are the repository previews. Edit [platform.json](platform.json) for labels, explicit routes and geometry; [platform.py](platform.py) renders the same reviewed model into both formats. Colors, card typography and original line icons are shared across all five views.

With Python, Pillow and Segoe UI or DejaVu Sans/Mono installed, run from the repository root:

```bash
python docs/diagrams/platform.py
python docs/diagrams/platform.py --view platform-serving
```

Windows uses Segoe UI/Consolas; Linux uses DejaVu Sans/Mono. Fonts are not redistributed. Line wrapping uses the installed font, so rendering on a different system can produce small typographic differences. SVG text also retains these fallback families. These are documentation renders; they do not contact the platform, refresh data or run inference.
