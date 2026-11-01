# Architecture views

One system map and nine detailed drawings explain the IoT platform. Native Excalidraw files keep shapes, text, logos and arrow bindings editable. All ten views share pastel responsibility frames, embedded product marks, readable source examples and numbered handoffs. Deployment and code views retain explicit workload/process boundaries. PNG previews and animated GIFs work directly on GitHub; SVG exports retain vectors and embedded fonts.

Open [architecture.html](architecture.html) from a local clone for the **animated presentation**. It works offline: choose a view, play or pause, change speed, zoom, and select or click an arrow to inspect its direction, meaning and source files. GitHub displays the HTML source; download or open the local file to use the viewer.

Moving dots illustrate handoff direction. Their timing is arbitrary and each loop is independent. They are **not live telemetry**, a sequence diagram, a throughput measurement or evidence that all jobs run concurrently. Animation belongs to the HTML and animated SVG exports; the Excalidraw editor contains the static editable drawing. Reduced-motion preferences start the viewer paused.

| View | Purpose | Editable source | Exports |
|---|---|---|---|
| System map | Data governance, batch/stream jobs, feature stores, training, release, inference, security, telemetry and agents in one view | [Excalidraw](platform-system.excalidraw) | [PNG](../images/platform-system.png) · [SVG](../images/platform-system.svg) · [Motion](../images/platform-system.animated.svg) · [GIF](../images/platform-system.gif) |
| 01 · Platform overview | Separate large-source, ML and streaming cohorts; scheduling, training and release | [Excalidraw](platform-overview.excalidraw) | [PNG](../images/platform-overview.png) · [SVG](../images/platform-overview.svg) · [Motion](../images/platform-overview.animated.svg) · [GIF](../images/platform-overview.gif) |
| 02 · Prediction request | Gateway, Feast lookup, eligibility gates and pinned model response | [Excalidraw](platform-serving.excalidraw) | [PNG](../images/platform-serving.png) · [SVG](../images/platform-serving.svg) · [Motion](../images/platform-serving.animated.svg) · [GIF](../images/platform-serving.gif) |
| 03 · Deployment topology | 42 selected resources: two PC clients and 40 workloads/processes/state resources, grouped by physical host and runtime | [Excalidraw](platform-deployment.excalidraw) | [PNG](../images/platform-deployment.png) · [SVG](../images/platform-deployment.svg) · [Motion](../images/platform-deployment.animated.svg) · [GIF](../images/platform-deployment.gif) |
| 04 · Observability and retraining | Distinct metric, log and trace paths; guarded candidate training | [Excalidraw](platform-monitoring.excalidraw) | [PNG](../images/platform-monitoring.png) · [SVG](../images/platform-monitoring.svg) · [Motion](../images/platform-monitoring.animated.svg) · [GIF](../images/platform-monitoring.gif) |
| 05 · RAG and agents | Verified reference state, coordinator delegation, MCP/API calls and shared GPU generation | [Excalidraw](platform-llm-plan.excalidraw) | [PNG](../images/platform-llm-plan.png) · [SVG](../images/platform-llm-plan.svg) · [Motion](../images/platform-llm-plan.animated.svg) · [GIF](../images/platform-llm-plan.gif) |
| 06 · Agent release admission | Complete policy input, fail-closed decision and exact-input recheck | [Excalidraw](agent-admission.excalidraw) | [PNG](../images/agent-admission.png) · [SVG](../images/agent-admission.svg) · [Motion](../images/agent-admission.animated.svg) · [GIF](../images/agent-admission.gif) |
| 07 · Assistant code boundaries | Five implemented classes, local SDK calls and cross-process HTTP | [Excalidraw](assistant-classes.excalidraw) | [PNG](../images/assistant-classes.png) · [SVG](../images/assistant-classes.svg) · [Motion](../images/assistant-classes.animated.svg) · [GIF](../images/assistant-classes.gif) |
| 08 · Agent trace paths | Logical delegation, shared generation and the exact private OTLP route | [Excalidraw](agent-traces.excalidraw) | [PNG](../images/agent-traces.png) · [SVG](../images/agent-traces.svg) · [Motion](../images/agent-traces.animated.svg) · [GIF](../images/agent-traces.gif) |
| 09 · Prompt policy cohort | Reject before generation and reconcile the original controlled cohort | [Excalidraw](prompt-policy.excalidraw) | [PNG](../images/prompt-policy.png) · [SVG](../images/prompt-policy.svg) · [Motion](../images/prompt-policy.animated.svg) · [GIF](../images/prompt-policy.gif) |

## Reading the drawings

Shapes distinguish triggers, processes, state and admission. Free-standing text provides runtime and scope details; frames group a host or runtime rather than introducing a service. The deployment inventory intentionally selects resources from a larger cluster: a WorkerPool can own several Pods, and the observer/turn adapter is one Deployment with two containers. SDKs run inside consuming jobs or APIs.

The system map's 47 nodes are logical roles and persisted state, not an inventory of 47 Deployments. Its Spark symbol covers separate bounded jobs; its Feast symbol covers separate batch materialization and serving preparation. Controller/UI are grouped as an application boundary. R2 follows job gauges through Pushgateway to Prometheus; view 04 expands that intermediary. The 58 numbered handoffs have their own source references. G0/G5/G6 explicitly connect retrieval/coordinator/drift generation to the same llm-d route. Every Redis role is online; batch names its producer, while Feast's offline history stays in Parquet. Pastel frames delimit responsibilities; they are not extra services.

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
| System map | D: persisted data; S: stream replay/writes; F: feature delivery; O: scheduling; Q: metadata; T: training; C: delivery; I: infrastructure; V: secrets; P: prediction; A: delegation/tools; K: knowledge; M: tool/query paths; G: generation; H/L: telemetry |
| Overview | D1–D2: Bronze/Silver source; F1–F3: separate ML cohort and materialization; C1/G1: orchestration and metadata; T0–T2/C2–C3: training and release; S0–S4: retained stream replay and writes |
| Prediction | U1–U2: authenticated request; U3–U4: online lookup and values; U5–U6: model call and result; U7: logical response through the gateway |
| Deployment | U1: browser to IAP forwarding; U2: SSH to the VM viewer; U3: CA/SNI-verified upstream HTTPS; G1–G2: model request; G3–G4: token stream and observed return |
| Observability | H1/M1/M3: returned metric data; L1–L3: collected logs and query results; M2: PromQL response; O1–O2: projected traces and protected query response; R1–R4: drift report, admission and candidate artifacts |
| RAG/agents | K0–K2: knowledge preparation and per-replica state; A1–A4: parent dispatch and logical specialist delegation; M1–M4: tool/API requests; M5: Drift MCP's 32-machine Feature API cohort; G1/G6/G7: retrieval/coordinator/drift generation; G2–G5: common inference route and return stream |
| Agent admission | C1–C2: complete rendered/live input and OPA decision; C3: deny before rollout; C4–C6: seal, immediate exact-input recheck, deploy and verify |
| Assistant classes | Q1: MCP request; C1–C2: local reader/encoder calls; D1: embedded records; H1/H3: private API requests; H2: injected backend |
| Agent traces | A1–A3: dispatch/delegation; M1–M2: actual tool cohort; G1–G7: shared generation and return; T0–T2: representative actor export, exact OTLP route and approved Jaeger projection |
| Prompt policy | P1: fixed original cases; P2–P3: reject/admit; P4–P5: reconcile the distinct outcomes; P6: exact batch statistics and separately interpreted rate graph |

## Source decisions

| Boundary | Implementation | Consequence for the drawing |
|---|---|---|
| Data scale | [Ingest report](../full-scale-ingest.md), [feature cohort](../medium-feature-dataset.md) | 102.45 GB covers Bronze/Silver. The separate 60-day ML source has 3,456,000 snapshots and 3,132,184 training rows. It is not a 100 GB end-to-end ML run. |
| Offline → online stream | [OnlineStage](../../feature_store/staged_push.py) | System-map S4 (overview S3) carries validated Parquet history and the registry/release pin. The online job selects the latest vector from this history. An acknowledged replay skips writes and does not renew Redis TTL. |
| Release ordering | [Airflow DAG](../../airflow/dags/iot_data_pipelines.py), [publisher](../../pipelines/airflow_tasks.py) | Validation and metadata publication gate downstream release. Small Airflow cohorts and the 100 GB ingest are separate runs. |
| Prediction | [Feature API](../../services/feature_api/runtime.py), [binding](../../services/predictor/binding.py) | Validate schema, clocks, state, lease and exact pin before accepting the `iot-pump-failure-v1` result. U7 returns through the gateway. |
| One cluster, two hosts | [GPU Deployment](../../infra/charts/iot-gpu-multimodel/templates/model.yaml) | The GPU is a K3s worker in the same cluster as the CPU server. One GPU and `Recreate` do not provide seamless rollout or host-level HA. The earlier Docker benchmark is a separate experiment. |
| Private browser access | [Gateway viewer](../../scripts/view-agent-gateway.py) | The browser uses loopback HTTP over IAP. The VM viewer independently verifies CA and SNI on its upstream HTTPS hop. Browser UI is not labelled HTTPS. |
| Trace store | [Jaeger manifest](../../infra/observability/jaeger.yaml), [observer](../../llm_observability/app.py) | Jaeger is a K3s Deployment with bounded in-memory storage. Restart can lose trace history. Query-result flow is distinct from the initiating request. |
| Retrieval state | [MCP chart](../../infra/charts/iot-mcp/templates/workload.yaml), [KnowledgeReader](../../rag/retrieval.py) | Each retrieval Pod receives a verified state copy. Feast and embedded Milvus Lite run in the consumer, without a standalone Milvus server. |
| Drift inputs | [MCPRuntime.drift](../../services/mcp_tools/runtime.py) | Fetch 32 machine snapshots from Feature API, require a shared source identity, then call `/v1/drift:compare`. M5 makes this dependency explicit. |
| Drift statistics | [Deployed detector](../../services/drift_api/detector.py), [MMD study](../joint-cohort.md) | Drift API/MCP return PSI diagnostics. Offline MMD research is separate and is not labelled as a statistic returned by the deployed tool. |
| Retraining and CI admission | [Retraining controller](../../ml/drift_retrain.py), [Rego gate](../../infra/policy/deployment.rego) | Retraining creates a candidate after admission checks; it does not automatically promote serving. The OPA gate is a CI policy check, not a cluster admission webhook. |
| Infrastructure | [Terraform](../../infra/gcp/vm/main.tf), [host playbook](../../infra/ansible/bootstrap.yml) | Compute VMs host the platform. The GPU joins the same K3s cluster; GKE and a managed lakehouse are not part of this implementation. |
| Delegation and acceptance | [Child checker](../../agents/delegation_acceptance.py), [answer checker](../../agents/answer_acceptance.py) | Specialist results return to the coordinator and through controller/UI. A task labelled completed does not establish a successful child or a correct answer. |

The [architecture reference](../architecture.md), [LLM reference](../llm-platform.md) and [requirement map](../final-audit-matrix.md) retain measurement and evidence boundaries. These authored drawings do not modify or replace original screenshots, failed outcomes or native receipts.

## Edit and export

Open any `.excalidraw` file in Excalidraw. Product marks are embedded image elements; components, text and bound arrows remain separate. The official Feast mark is a PNG logo, while the other marks are SVG. The scene itself is never flattened into an image. Source references live in each element's `customData`. When editing, keep the white canvas at `(0, 0)`, preserve semantic IDs, and reroute arrows around labels and components.

To reproduce exports with Node.js and a Playwright-supported browser:

```bash
cd docs/diagrams
npm ci
npx playwright install chromium
npm run render
python -m pip install -r requirements.txt
node preview.mjs --all
npm run check -- --browser --output ../../outputs/architecture-check/report.json
```

The first dependency/browser installation needs a network connection. Rendering and viewing use local assets and make no platform, model or cloud requests. `export.mjs` uses the pinned Excalidraw 0.18.0 renderer, embeds fonts/logos, and derives every motion path from the native arrow's actual point order. It does not generate the architecture layout. `--write-native` additionally normalizes the edited scenes through Excalidraw and embeds the reviewed icon assets. The verification output must use a new path under `outputs/`.

The checker validates native element IDs, bindings, source paths, image assets, dimensions, component/text overlap, routed arrows and all 155 motion paths across ten views. Browser checks exercise every handoff detail, real particle movement, pause/resume, speed, zoom, path clicks, reduced motion, mobile layout and absence of external requests. These are documentation checks, not a fresh workload run or coursework grade.

`preview.mjs --all` captures each authored SVG and `encode-preview.py` encodes its 36 frames into a six-second GIF. Use `--view platform-serving` to select one view and `--force` to recapture a previously verified preview. An unchanged source/preview hash is reused byte for byte. Independent staggered phases have arbitrary timing; this does not record an application session. A fixed palette reserves source colors to preserve small product marks. Each adjacent `.preview.json` manifest binds exact GIF bytes and SVG text with CRLF canonicalized to LF. Every embedded preview selects the PNG for reduced-motion visitors. Use `DIAGRAM_PYTHON` if the Python interpreter with Pillow is not named `python`.

The four former Mermaid flowcharts are now native Excalidraw views 06–09. The classes view distinguishes local calls from HTTP; the trace view is a documented recorded cohort rather than current telemetry. OPA input and prompt-policy outcomes remain tied to their original reports. Statistical plots, native screenshots, source workbooks and experiment evidence retain their original bytes and format.

## Assets and attribution

Product marks include [Simple Icons 15.0.0](https://github.com/simple-icons/simple-icons/tree/15.0.0), under [CC0](icons/CC0-1.0.txt), and official [Airflow](https://airflow.apache.org/community/resources/), [DataHub](https://github.com/datahub-project/datahub/tree/master/docs-website/static/img), [Kubeflow](https://github.com/kubeflow/website/tree/master/static/images) and [Feast](https://github.com/feast-dev/feast/tree/master/docs/assets) assets from their Apache-licensed projects. The [Apache license](icons/Apache-2.0.txt), exact asset bodies, collection/source URLs and SHA-256 hashes are retained in [brands.json](icons/brands.json). Product trademarks remain with their owners.

Original generic role icons identify MCP, llm-d, KServe, Alloy and Loki; these are not presented as official logos. All views use the reviewed official Feast/DataHub marks where those products appear. The release recheck uses an original generic lock icon, not an OPA product mark. Brand colors belong to the marks; frame colors explain responsibilities. The diagram does not import another project's storage, CDC or query technologies merely to match its visual style.

Cascadia Code is embedded in SVG exports under Microsoft's [SIL Open Font License](icons/Cascadia-OFL-1.1.txt), sourced from the [Cascadia Code release](https://github.com/microsoft/cascadia-code/tree/v2407.24). Excalidraw is the rendering/editing format; these drawings do not imply vendor endorsement. The retired Pillow renderer is available in Git history; the native Excalidraw scenes are the current source of truth.
