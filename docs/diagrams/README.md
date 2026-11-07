# Architecture diagrams

Start with the [main platform architecture](../images/platform-architecture.svg) ([editable Excalidraw](platform-architecture.excalidraw), [GIF](../images/platform-architecture.gif), [motion SVG](../images/platform-architecture.animated.svg)). It groups components into data processing, storage and features, model lifecycle and serving, knowledge and agents, and shared operations. The README uses the moving-dot GIF with a PNG fallback for reduced motion. The [current execution guide](execution-guide.md#main-platform-architecture) explains its source decisions and retains the detailed B1, S1, T1, P1, K1, A1 and D1 views.

The main view gives online lookup, Retrieval MCP and Drift MCP separate Feature API ports and connector lanes. Training history enters KFP from the left edge, clear of the subsystem title. Each specialist sits above its MCP service; all three agent roles use the shared LLM band below them. The coordinator is labelled as an agent that delegates through kagent. The finalized streaming path explicitly includes the staged offline/online writers.

In this main view, blue denotes application calls/events; green denotes data, artifacts and returned values; purple denotes ML or LLM inference; and orange dashed lines denote release/control. Double-headed connectors summarize a call and its return. Bridges/gaps indicate crossings without a connection. The static PNG and SVG omit animation markers; only the motion SVG and GIF contain moving dots.

To rebuild this standalone main view without changing the retained interactive viewer, run `node docs/diagrams/export.mjs --standalone --view platform-architecture --write-native`, followed by `node docs/diagrams/preview.mjs --standalone --view platform-architecture`. Use the pinned renderer dependencies described below; `DIAGRAM_PYTHON` selects a Python interpreter with Pillow.

The [medallion table model](../images/data-model-zones.svg) ([PNG](../images/data-model-zones.png), [Excalidraw](data-model-zones.excalidraw), [moving-dot SVG](../images/data-model-zones.animated.svg)) is a separate data-model view: all thirteen original-batch tables, their grains and transformation inputs. Its [canonical storage chapter](../edai-1/data_storage.md#schema-design) explains key cardinality, the native SQL relationship proof and the distinct staged Airflow layout. It is not an execution-order or enforced-constraint diagram; moving dots illustrate direction only.

## Earlier architecture set

Eight earlier Excalidraw views capture the previous architecture review. The system map summarizes dependencies, a C4 container view zooms into feature access, five dynamic views trace numbered processing and request paths, and the deployment view locates workloads and state on the recorded CPU/GPU hosts. This follows the separation in the C4 [container](https://c4model.com/diagrams/container), [dynamic](https://c4model.com/diagrams/dynamic) and [deployment](https://c4model.com/diagrams/deployment) views.

The drawings describe the repository and its recorded lab. They do not establish current service readiness, simultaneous execution of every workload or a production high-availability topology.

## Three layers

| Layer | View and question | Native source | Full-size exports |
|---|---|---|---|
| 1 · System | How do data, ML, serving, agents and operating controls fit together? | [System](architecture-system.excalidraw) | [PNG](../images/architecture-system.png) · [SVG](../images/architecture-system.svg) · [GIF](../images/architecture-system.gif) |
| 1 · Containers | Which applications read history, write online values and make predictions? | [Feature access](feature-containers.excalidraw) | [PNG](../images/feature-containers.png) · [SVG](../images/feature-containers.svg) · [GIF](../images/feature-containers.gif) |
| 2 · Batch | How do DP1-DP3 turn raw exports into validated histories and separate labels? | [Batch flow](flow-batch.excalidraw) | [PNG](../images/flow-batch.png) · [SVG](../images/flow-batch.svg) · [GIF](../images/flow-batch.gif) |
| 2 · Streaming | Where do dedup, watermarks, finalized windows and staged writers run? | [Stream flow](flow-stream.excalidraw) | [PNG](../images/flow-stream.png) · [SVG](../images/flow-stream.svg) · [GIF](../images/flow-stream.gif) |
| 2 · Training | Which files reach KFP, where are labels joined, and what gates serving? | [Training flow](flow-training.excalidraw) | [PNG](../images/flow-training.png) · [SVG](../images/flow-training.svg) · [GIF](../images/flow-training.gif) |
| 2 · Prediction | What happens between a machine request and its checked response? | [Prediction flow](flow-prediction.excalidraw) | [PNG](../images/flow-prediction.png) · [SVG](../images/flow-prediction.svg) · [GIF](../images/flow-prediction.gif) |
| 2 · Agents | How do delegation, MCP facts and all three generation routes interact? | [Agent flow](flow-agents.excalidraw) | [PNG](../images/flow-agents.png) · [SVG](../images/flow-agents.svg) · [GIF](../images/flow-agents.gif) |
| 3 · Deployment | Which workloads, processes and persistent state belong to each host/runtime? | [Deployment](deployment-runtime.excalidraw) | [PNG](../images/deployment-runtime.png) · [SVG](../images/deployment-runtime.svg) · [GIF](../images/deployment-runtime.gif) |

Each arrow has a native `customData` record with its endpoints, meaning, transport/dependency type, source files and qualifications. The [layer crosswalk](layers.json) binds the eight views to their implementation and maps the selected deployment inventory into named groups.

## Reading the drawings

The system map groups related workloads and labels the selected release. The feature-access container view uses boxes for executable applications or persisted stores. It places Feast and DuckDB inside the callers that load or serve features. The flow views expand actions such as lookup and result validation within the Feature API, and deployment frames locate resources by host and runtime. A WorkerPool owns worker Pods; the observer Deployment contains two containers.

| Cue | Meaning |
|---|---|
| Orange input or ellipse | Caller, source or private access |
| Blue process | Data job, application/API or processing step |
| Green state or result | Persisted history, online store, verified artifact or returned data |
| Purple | Training, agent or model generation |
| Yellow decision | Eligibility or candidate admission |
| Solid arrow | Directed data, artifact, request, response or SDK dependency; label gives intent |
| Dashed arrow | Orchestration, release, secret synchronization or explicitly logical A2A delegation |
| Dashed deployment frame | A host/runtime grouping, not another service |
| Dark example panel | Actual field names, paths or API contract; authored illustration, not a service screenshot |

Arrow IDs are local to a view. Crossings without a component do not join flows. Request and response paths are separate where the return affects validation or attribution. At the system level, a dependency can summarize a request/return pair that its flow view expands.

Moving dots indicate authored direction with arbitrary independent timing. They are not live traffic, a throughput measurement, an execution schedule or proof of concurrency. Animation belongs to the SVG/GIF/local viewer; native Excalidraw drawings remain static and editable.

## Source decisions

| Boundary | Implementation | What the diagrams show |
|---|---|---|
| Offline and online stores | [Batch source](../../feature_store/repository.py), [materializer](../../feature_store/materialize.py), [serving preparation](../../feature_store/serving.py) | Historical Parquet through embedded Feast/DuckDB; registry metadata describes its sources. The system and container views select one release Redis, with separate monitoring and prediction projects. |
| Scale | [Large ingest](../edai-1/full_scale_ingest.md), [ML cohort](../edai-1/medium_feature_dataset.md) | The 100GB milestone ends at Bronze/Silver. Feature engineering/training use a separate 60-day source. |
| Data correction | [Transforms](../../pipelines/transforms.py), [features](../../pipelines/features.py) | Explicit union schema, conflict rejection, deterministic window dedup, temporal dimensions, arrived-history features and separately censored labels. |
| Airflow release | [DAGs](../../airflow/dags/iot_data_pipelines.py), [metadata publisher](../../pipelines/metadata_publish.py) | Publication runs with ALL_DONE to retain failure outcomes; the next DAG requires validation and publication success. |
| Stream semantics | [Flink job](../../pipelines/stream_job.py), [ledger/sink](../../pipelines/stream_contract.py) | Fifteen-minute watermark delay, ten-minute windows, processing-time cache expiry and durable event ledger. SQLite writes are not checkpoint-transactional; no general exactly-once claim. |
| Stream delivery | [Staged writers](../../feature_store/staged_push.py) | Offline history completes first; the online writer verifies it and chooses latest values. Acknowledged replay skips writes and TTL renewal. Four monitoring aggregates do not replace eight prediction inputs. |
| KFP inputs | [Native load](../../ml/notebook_load.py), [PVC configuration](../../ml/kfp_cluster.py) | Mounted Parquet/registry; Feast queries contain only machine ID/time; labels are exported as id/label and joined one-to-one after retrieval. |
| Versioning and promotion | [Five-stage pipeline](../../ml/kfp_versioned.py), [dataset versions](../../ml/mlflow_data_versions.py), [serving CI](../../scripts/ci-serving.py) | Incremental data publication, evaluated model candidate and separately selected/tested serving release. Candidate creation does not automatically promote serving. |
| Prediction | [API runtime](../../services/feature_api/runtime.py), [model pin](../../services/predictor/binding.py) | Native values/timestamps, both clocks, eligibility and exact model provenance are checked; the response returns through API/gateway. |
| Agent tools | [Agent definitions](../../infra/charts/iot-agents/templates/agents.yaml), [MCP runtime](../../services/mcp_tools/runtime.py) | Controller-routed delegation; specialist tool allowlists; per-Pod embedded reference state; 32–200 bounded API snapshots before PSI comparison (recorded cohort: 32). |
| Shared generation | [ModelConfig](../../infra/agents/kagent-values.yaml), [observer](../../infra/charts/iot-llm-observer/templates/workload.yaml), [control proxy](../../tools/model-control/main.go) | All three roles reach llm-d, then observer/turn adapter and control, then the private GPU llama.cpp server; native replies return through the same route. |
| Private access and traces | [Viewer](../../scripts/view-agent-gateway.py), [trace bridge](../../infra/llm_observer_routing.py) | PC loopback/IAP and verified upstream HTTPS are distinct. OTLP uses the exact private trace route, separate from model selection. |

The [architecture narrative](../edai-1/architecture.md), [LLM reference](../edai-2/llm_platform.md) and linked technical reports retain the experiment scopes, original failures and recorded execution evidence. Source references establish implementation correspondence; a rendered arrow is not execution evidence.

## Deployment groups

The deployment view groups the earlier 42 selected resource/client roles into the locations below. The mapping is explicit in [layers.json](layers.json); grouping does not remove their identities or imply one Pod per group. The CPU disk group adds the shared persistence boundary.

| Location | Selected groups and resources |
|---|---|
| Operator PC | Browser/API client and IAP/SSH forwarding process |
| CPU host loopback | Bounded viewer processes; distinct from NGINX |
| CPU K3s · data/serving | KFP API/task Pods and Spark driver/executors; Feature/Drift APIs, KServe predictor and serving Redis |
| CPU K3s · agents | Controller, UI, catalog, PostgreSQL; coordinator/retrieval/drift WorkerPools; both MCP Deployments |
| CPU K3s · substrate | Six Valkey members and RustFS snapshot volumes; substrate routing/identity controls |
| CPU K3s · inference/edge | llm-d router/EPP; observer and model-control containers; NGINX private gateway; bounded-memory Jaeger |
| CPU Docker | Airflow API/scheduler/DAG processor and DB; DataHub GMS/frontend and stores; MLflow/SQLite/artifacts; Jenkins; Spark/Kafka/Flink and Feast jobs |
| CPU host services | Prometheus, Grafana, Alloy, Loki, Pushgateway, node exporter, Vault and scoped consumers |
| CPU disks | Persistent datasets, registries, CAS, journals, DB/model state; separate SSD scratch; bounded PVC/bind mounts |
| GPU worker · same cluster | One llama.cpp Deployment, one V100, private ClusterIP service, reserved node selector and read-only host model files |

### Redis instance and project mapping

| Recorded path | Redis address | What is separated |
|---|---|---|
| Batch materialization and original stream push | Docker `iot-feast` service `online:6379` | One server. The batch project and per-source stream project use separate keys. [Compose](../../infra/feast.compose.yaml) · [Stream runner](../../scripts/run-stream-push.py) |
| Staged stream CI and the selected serving release | Kubernetes `online-serve-jenkins-8` in `iot-features` | One release instance. The CI writer uses the monitoring project; the Feature API uses the serving project. [CI target](../../scripts/ci-stream-push.py) · [Refresh target](../../scripts/refresh-agent-features.py) |
| Other serving releases | `online-<release>` | Serving CI creates a Redis Deployment and Service per release; older instances are outside the selected view. [Release implementation](../../ml/serving_ci.py) |

The two environments are recorded configurations. This inventory does not assert that each instance is running now. Substrate's Valkey members are separate agent state, outside the feature-store data path.

The Feature API seeds a small memory-backed state copy rather than mounting bulk training data. Retrieval MCP copies its own verified knowledge state. GPU model files live on the GPU host. Jaeger is memory-backed and can lose traces on restart. A stopped VM, a retained disk and an unexpired feature lease are different states. Neither same-host replicas/executors nor one GPU establish host-level HA; Recreate can interrupt inference.

## Reading size

The primary system view uses 28px text on a 1,600px canvas (about 15.7px at 896px display width). Flow/deployment text is at least 22px at native size. Use full-resolution PNG/SVG for inspection rather than compressing every implementation detail into the README preview.

Open [architecture.html](architecture.html) from a local clone. The selector groups **Layer 1**, **Layer 2**, **Layer 3** and reference views; it starts with the system architecture. **Read labels** zooms to at least natural width and a minimum 16px text size; scroll inside the canvas. **Fit** restores the full composition. Play/pause, speed and arrow details work offline. Reduced-motion preferences start the viewer paused and select static PNGs in embedded previews. GitHub displays HTML source, so use the local file for interactive controls.

Native screenshots remain original. Before/after Spark captures use one full-width image per row. Separate readable benchmark panels are derived from unchanged native reports, including the slower aggregate dedup candidate.

## Edit and export

Edit shapes, text, logos and bound arrows in the `.excalidraw` source. Components, labels and marks remain separate native elements; no scene is a flattened screenshot. Keep semantic IDs, source references, canvas origin, readable text sizes and arrow routing around unrelated elements. Product logos are embedded; the official Feast mark is a PNG logo, while other marks are SVG.

From `docs/diagrams`, install the pinned renderer once and select the view being changed:

```bash
npm ci
npx playwright install chromium
npm run render -- --view flow-training --write-native
python -m pip install -r requirements.txt
node preview.mjs --view flow-training
npm run check -- --browser --output ../../outputs/architecture-check/report.json
```

The renderer uses Excalidraw 0.18.0 and derives motion from authored arrow points. It does not invent a layout. Repeat the selected export for each edited view; `preview.mjs --all` reuses byte-identical previews whose source hashes match. Each preview has 36 distinct frames over six seconds and an adjacent source/byte manifest. `DIAGRAM_PYTHON` selects an interpreter with Pillow. Dependencies/browser installation needs a network connection; rendering/viewing makes no platform or model requests.

From the repository root, `python -B -S scripts/verify-architecture-layers.py` checks layer membership, source bindings, critical storage/tool/training boundaries, deployment coverage and authored artifact identities. The diagram checker separately validates actual geometry, source paths, native bindings, motion, controls, desktop/mobile behavior and reduced-motion fallbacks. These checks support review; they do not certify a grade or current runtime health.

## Reference views

Earlier source-bound layouts and focused technical studies remain available for continuity. The eight views above belong to the earlier architecture set. The earlier diagrams preserve their historical layouts and labels; use the Redis mapping above for the selected release.

<details>
<summary>Open earlier layouts and focused studies</summary>

| View | Source | Full-size image |
|---|---|---|
| Earlier complete poster | [System](platform-system.excalidraw) | [PNG](../images/platform-system.png) |
| Earlier Data/ML overview | [Overview](platform-overview.excalidraw) | [PNG](../images/platform-overview.png) |
| Earlier prediction layout | [Serving](platform-serving.excalidraw) | [PNG](../images/platform-serving.png) |
| Individual 42-resource inventory | [Deployment](platform-deployment.excalidraw) | [PNG](../images/platform-deployment.png) |
| Observability and guarded retraining detail | [Monitoring](platform-monitoring.excalidraw) | [PNG](../images/platform-monitoring.png) |
| Earlier RAG/agent layout | [LLM](platform-llm-plan.excalidraw) | [PNG](../images/platform-llm-plan.png) |
| OPA release admission detail | [Admission](agent-admission.excalidraw) | [PNG](../images/agent-admission.png) |
| Assistant code boundaries | [Classes](assistant-classes.excalidraw) | [PNG](../images/assistant-classes.png) |
| Agent trace routes | [Traces](agent-traces.excalidraw) | [PNG](../images/agent-traces.png) |
| Controlled prompt-policy cohort | [Policy](prompt-policy.excalidraw) | [PNG](../images/prompt-policy.png) |
| Compact introduction | [Three workflows](platform-reading-map.excalidraw) | [PNG](../images/platform-reading-map.png) |

</details>

## Assets and attribution

Product marks include [Simple Icons 15.0.0](https://github.com/simple-icons/simple-icons/tree/15.0.0), under [CC0](icons/CC0-1.0.txt), and official [Airflow](https://airflow.apache.org/community/resources/), [DataHub](https://github.com/datahub-project/datahub/tree/master/docs-website/static/img), [Kubeflow](https://github.com/kubeflow/website/tree/master/static/images) and [Feast](https://github.com/feast-dev/feast/tree/master/docs/assets) assets from their Apache-licensed projects. The [Apache license](icons/Apache-2.0.txt), exact asset bodies, collection/source URLs and hashes remain in [brands.json](icons/brands.json). Product trademarks remain with their owners.

Original generic icons identify roles such as MCP, llm-d and KServe; these are not official product marks. Color encodes responsibility, while a product mark retains its brand color. Source workbooks, native screenshots, measurements, failed outcomes and original receipt archives are unchanged by the new drawings.
