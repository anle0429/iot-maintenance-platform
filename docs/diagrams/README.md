# Architecture views

Five source-reviewed drawings explain the recorded IoT lab. Native Excalidraw files keep shapes, text, logos and arrow bindings editable. PNG previews work directly on GitHub; SVG exports retain vectors and embedded fonts.

Open [architecture.html](architecture.html) from a local clone for the **animated presentation**. It works offline: choose a view, play or pause, change speed, zoom, and select or click an arrow to inspect its direction, meaning and source files. GitHub displays the HTML source; download or open the local file to use the viewer.

Moving dots illustrate handoff direction. Their timing is arbitrary and each loop is independent. They are **not live telemetry**, a sequence diagram, a throughput measurement or evidence that all jobs run concurrently. Animation belongs to the HTML and animated SVG exports; the Excalidraw editor contains the static editable drawing. Reduced-motion preferences start the viewer paused.

| View | Purpose | Editable source | Exports |
|---|---|---|---|
| 01 · Platform overview | Separate large-source, ML and streaming cohorts; scheduling, training and release | [Excalidraw](platform-overview.excalidraw) | [PNG](../images/platform-overview.png) · [SVG](../images/platform-overview.svg) · [Motion](../images/platform-overview.animated.svg) |
| 02 · Prediction request | Gateway, Feast lookup, eligibility gates and pinned model response | [Excalidraw](platform-serving.excalidraw) | [PNG](../images/platform-serving.png) · [SVG](../images/platform-serving.svg) · [Motion](../images/platform-serving.animated.svg) |
| 03 · Deployment topology | 42 selected resources: two PC clients and 40 workloads/processes/state resources, grouped by physical host and runtime | [Excalidraw](platform-deployment.excalidraw) | [PNG](../images/platform-deployment.png) · [SVG](../images/platform-deployment.svg) · [Motion](../images/platform-deployment.animated.svg) |
| 04 · Observability and retraining | Distinct metric, log and trace paths; guarded candidate training | [Excalidraw](platform-monitoring.excalidraw) | [PNG](../images/platform-monitoring.png) · [SVG](../images/platform-monitoring.svg) · [Motion](../images/platform-monitoring.animated.svg) |
| 05 · RAG and agents | Verified reference state, coordinator delegation, MCP/API calls and shared GPU generation | [Excalidraw](platform-llm-plan.excalidraw) | [PNG](../images/platform-llm-plan.png) · [SVG](../images/platform-llm-plan.svg) · [Motion](../images/platform-llm-plan.animated.svg) |

## Reading the drawings

Shapes distinguish triggers, processes, state and admission. Free-standing text provides runtime and scope details; frames group a host or runtime rather than introducing a service. The deployment inventory intentionally selects resources from a larger cluster: a WorkerPool can own several Pods, and the observer/turn adapter is one Deployment with two containers. SDKs run inside consuming jobs or APIs.

| Visual cue | Meaning |
|---|---|
| Orange ellipse / accent | Input, scheduling or authenticated access |
| Blue | Data transformation, APIs and application services |
| Purple | ML tasks, agents and model generation |
| Green | Persisted state, artifacts or returned values |
| Yellow diamond | Admission decision |
| Dashed slate line | Orchestration or metadata handoff |
| Dark panel | Selected source code or recorded scope, not a runtime screenshot |

Arrows follow data, artifacts, requests, responses or control. A file dependency is not an RPC. A Prometheus-to-Grafana arrow is returned query data; Grafana initiates the query. Crossings without a component do not join flows. IDs are local to a view. The native files carry `customData` for each component and handoff; the viewer exposes each arrow's source references.

| View | Handoff IDs |
|---|---|
| Overview | D1–D2: Bronze/Silver source; F1–F3: separate ML cohort and materialization; C1/G1: orchestration and metadata; T0–T2/C2–C3: training and release; S0–S4: retained stream replay and writes |
| Prediction | U1–U2: authenticated request; U3–U4: online lookup and values; U5–U6: model call and result; U7: logical response through the gateway |
| Deployment | U1: browser to IAP forwarding; U2: SSH to the VM viewer; U3: CA/SNI-verified upstream HTTPS; G1–G2: model request; G3–G4: token stream and observed return |
| Observability | H1/M1/M3: returned metric data; L1–L3: collected logs and query results; M2: PromQL response; O1–O2: projected traces and protected query response; R1–R4: drift report, admission and candidate artifacts |
| RAG/agents | K0–K2: knowledge preparation and per-replica state; A1–A4: parent dispatch and logical specialist delegation; M1–M4: tool/API requests; M5: Drift MCP's 32-machine Feature API cohort; G1–G5: shared generation and return stream |

## Source decisions

| Boundary | Implementation | Consequence for the drawing |
|---|---|---|
| Data scale | [Ingest report](../full-scale-ingest.md), [feature cohort](../medium-feature-dataset.md) | 102.45 GB covers Bronze/Silver. The separate 60-day ML source has 3,456,000 snapshots and 3,132,184 training rows. It is not a 100 GB end-to-end ML run. |
| Offline → online stream | [OnlineStage](../../feature_store/staged_push.py) | S3 carries validated Parquet history and the registry/release pin. The online job selects the latest vector from this history. An acknowledged replay skips writes and does not renew Redis TTL. |
| Release ordering | [Airflow DAG](../../airflow/dags/iot_data_pipelines.py), [publisher](../../pipelines/airflow_tasks.py) | Validation and metadata publication gate downstream release. Small Airflow cohorts and the 100 GB ingest are separate runs. |
| Prediction | [Feature API](../../services/feature_api/runtime.py), [binding](../../services/predictor/binding.py) | Validate schema, clocks, state, lease and exact pin before accepting the `iot-pump-failure-v1` result. U7 returns through the gateway. |
| One cluster, two hosts | [GPU Deployment](../../infra/charts/iot-gpu-multimodel/templates/model.yaml) | The GPU is a K3s worker in the same cluster as the CPU server. One GPU and `Recreate` do not provide seamless rollout or host-level HA. The earlier Docker benchmark is a separate experiment. |
| Private browser access | [Gateway viewer](../../scripts/view-agent-gateway.py) | The browser uses loopback HTTP over IAP. The VM viewer independently verifies CA and SNI on its upstream HTTPS hop. Browser UI is not labelled HTTPS. |
| Trace store | [Jaeger manifest](../../infra/observability/jaeger.yaml), [observer](../../llm_observability/app.py) | Jaeger is a K3s Deployment with bounded in-memory storage. Restart can lose trace history. Query-result flow is distinct from the initiating request. |
| Retrieval state | [MCP chart](../../infra/charts/iot-mcp/templates/workload.yaml), [KnowledgeReader](../../rag/retrieval.py) | Each retrieval Pod receives a verified state copy. Feast and embedded Milvus Lite run in the consumer, without a standalone Milvus server. |
| Drift inputs | [MCPRuntime.drift](../../services/mcp_tools/runtime.py) | Fetch 32 machine snapshots from Feature API, require a shared source identity, then call `/v1/drift:compare`. M5 makes this dependency explicit. |
| Delegation and acceptance | [Child checker](../../agents/delegation_acceptance.py), [answer checker](../../agents/answer_acceptance.py) | Specialist results return to the coordinator and through controller/UI. A task labelled completed does not establish a successful child or a correct answer. |

The [architecture reference](../architecture.md), [LLM reference](../llm-platform.md) and [requirement map](../final-audit-matrix.md) retain measurement and evidence boundaries. These authored drawings do not modify or replace original screenshots, failed outcomes or native receipts.

## Edit and export

Open any `.excalidraw` file in Excalidraw. Logos are embedded SVG image elements; components, text and bound arrows remain separate. Source references live in each element's `customData`. When editing, keep the white canvas at `(0, 0)`, preserve semantic IDs, and reroute arrows around labels and components.

To reproduce exports with Node.js and a Playwright-supported browser:

```bash
cd docs/diagrams
npm ci
npx playwright install chromium
npm run render
npm run check -- --browser --output ../../outputs/architecture-check/report.json
```

The first dependency/browser installation needs a network connection. Rendering and viewing use local assets and make no platform, model or cloud requests. `export.mjs` uses the pinned Excalidraw 0.18.0 renderer, embeds fonts/logos, and derives every motion path from the native arrow's actual point order. It does not generate the architecture layout. `--write-native` additionally normalizes the edited scenes through Excalidraw and embeds the reviewed icon assets. The verification output must use a new path under `outputs/`.

The checker validates native element IDs, bindings, source paths, image assets, dimensions, component/text overlap, routed arrows and all 61 motion paths. Browser checks exercise every handoff detail, real particle movement, pause/resume, speed, zoom, path clicks, reduced motion, mobile layout and absence of external requests. These are documentation checks, not a fresh workload run or coursework grade.

## Assets and attribution

Product marks are drawn from [Simple Icons 15.0.0](https://github.com/simple-icons/simple-icons/tree/15.0.0), under [CC0](icons/CC0-1.0.txt). The exact SVGs, collection URLs and hashes are in [brands.json](icons/brands.json). Missing product marks use original generic role icons (for example Feast, DataHub, MCP, llm-d, KServe, Alloy and Loki); these are not presented as official logos. Brand colors belong to the marks; role colors explain the surrounding architecture.

Cascadia Code is embedded in SVG exports under Microsoft's [SIL Open Font License](icons/Cascadia-OFL-1.1.txt), sourced from the [Cascadia Code release](https://github.com/microsoft/cascadia-code/tree/v2407.24). Excalidraw is the rendering/editing format; these drawings do not imply vendor endorsement. The retired Pillow renderer is available in Git history; the native Excalidraw scenes are the current source of truth.
