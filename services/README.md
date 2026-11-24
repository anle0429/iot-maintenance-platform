# Runtime services

Each service exposes a bounded interface over domain logic or a pinned backend. HTTP validation and authentication belong at the application boundary; SDK/model/store behavior lives in the runtime and adapters.

| Service | Input → output | Main code | Runtime proof |
|---|---|---|---|
| Feature API | Machine ID → validated eight-feature trace / prediction | [app](feature_api/app.py), [runtime](feature_api/runtime.py) | [Feature API](../docs/edai-1/feature_api.md), [serving CI](../docs/edai-1/ci_serving.md) |
| Predictor | Validated feature vector → uncalibrated score and model identity | [app](predictor/app.py), [binding](predictor/binding.py), [runtime](predictor/runtime.py) | [Model serving](../docs/edai-1/model_serving.md) |
| Drift API | Fixed-reference cohort → replay-safe drift assessment | [app](drift_api/app.py), [runtime](drift_api/runtime.py), [detector](drift_api/detector.py) | [Drift/eventing](../docs/edai-1/drift_eventing.md) |
| A/B API | Machine request → version-attributed model response | [app](ab_api/app.py), [runtime](ab_api/runtime.py) | [A/B deployment](../docs/edai-1/ci_ab_deployment.md) |
| RAG Search API | Bounded question → ranked passages and their source revisions | [app](rag_search/app.py), [snapshot reader](rag_search/reader.py) | [v16 route and rollout status](../docs/architecture/ml-llm-platform-v16-notes.md) |
| MCP tools | Authenticated typed tool call → native feature/model/reference/drift facts | [factory](mcp_tools/app.py), [contracts](mcp_tools/contracts.py), [runtime](mcp_tools/runtime.py) | [MCP](../docs/edai-2/mcp_tools.md), [application design](../docs/edai-2/mcp_application_design.md) |

Helm, images, dependency pins and gateway policy are under [infra](../infra/). Tests use controlled adapters at external boundaries; separate native recordings establish deployed behavior. See the [command index](../scripts/README.md) and [operations runbook](../docs/edai-1/operations.md) for execution requirements.

Retrieval MCP reports pod readiness after the local transport and credentials initialize. Each tool still checks its own upstream when called. The authenticated `/health/dependencies` endpoint checks both the Feature API and the RAG Search API, and native release and scaling probes use it before sending their workloads. The Search init container rebuilds the replica's writable copy from the sealed snapshot on Pod initialization, including after a node restart.

Each Search pod runs one query at a time and admits one waiting query for up to one second. Its `/metrics` endpoint records authenticated, schema-valid arrivals before admission, including requests that receive HTTP 429. It also reports active and queued work and bounded outcome counters. In the request-based Helm profile, KEDA scales the Search Deployment on this per-pod arrival rate. The existing `autoscaling.requestsPerSecondPerReplica` value remains the default threshold for Search and its `search.requestsPerSecondPerReplica` override can be set independently. Prepare the pod scrape job with [the Prometheus configuration renderer](../scripts/render-rag-search-prometheus.py), using the existing private MCP discovery identity. Apply it through the existing Prometheus configuration reload and check for `iot-rag-search` targets before measuring request-based scaling. Drift MCP retains its existing successful-tool rate.
