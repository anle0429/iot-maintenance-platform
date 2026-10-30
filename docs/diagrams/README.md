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
| Overview | D1–D2: large-source ingestion; F1–F3: separate ML source/materialization; C1/G1: control and metadata; T0–T2/C2–C3: training/release; S0–S4: stream replay/writes |
| Prediction | U1–U2: incoming request; U3–U4: online lookup/values; U5–U6: model request/result; U7: logical response through the gateway |
| Deployment | U1–U2: browser, loopback viewer and IAP-backed access; G1–G2: router/observer/private GPU request; G3: token stream |
| Observability | H1/M1/M3: metric sources; L1–L3: logs/query results; M2: metric queries; O1–O2: traces/private viewer; R1–R4: drift gauges and admitted training |
| RAG/agents | K0–K2: reference preparation/retrieval; A1–A4: controller/delegation; M1–M4: MCP/API calls; G1–G5: shared generation and return stream |

The 102.45 GB result covers Bronze/Silver, while feature engineering and ML use a separate 60-day cohort. The serving pin remains model 6; model 7 is a candidate. Stream aggregates have a different schema. Additional worker replicas and the separate GPU host do not establish host-level HA. See [architecture](../architecture.md) and the [evidence map](../final-audit-matrix.md) for implementation and measurement boundaries.

## Vector source and rendering

The SVG files retain editable text, paths and semantic groups (`node-*`, `edge-*`, `boundary-*`). They contain no external images or font downloads. PNG files are the repository previews. Edit [platform.json](platform.json) for labels, explicit routes and geometry; [platform.py](platform.py) renders the same reviewed model into both formats. Colors, card typography and original line icons are shared across all five views.

With Python, Pillow and Segoe UI or DejaVu Sans/Mono installed, run from the repository root:

```bash
python docs/diagrams/platform.py
python docs/diagrams/platform.py --view platform-serving
```

Windows uses Segoe UI/Consolas; Linux uses DejaVu Sans/Mono. Fonts are not redistributed. Line wrapping uses the installed font, so rendering on a different system can produce small typographic differences. SVG text also retains these fallback families. These are documentation renders; they do not contact the platform, refresh data or run inference.
