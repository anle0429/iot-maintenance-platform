# Runtime services

Each service exposes a bounded interface over domain logic or a pinned backend. HTTP validation and authentication belong at the application boundary; SDK/model/store behavior lives in the runtime and adapters.

| Service | Input → output | Main code | Runtime proof |
|---|---|---|---|
| Feature API | Machine ID → validated eight-feature trace / prediction | [app](feature_api/app.py), [runtime](feature_api/runtime.py) | [Feature API](../docs/feature-api.md), [serving CI](../docs/ci-serving.md) |
| Predictor | Validated feature vector → uncalibrated score and model identity | [app](predictor/app.py), [binding](predictor/binding.py), [runtime](predictor/runtime.py) | [Model serving](../docs/model-serving.md) |
| Drift API | Fixed-reference cohort → replay-safe drift assessment | [app](drift_api/app.py), [runtime](drift_api/runtime.py), [detector](drift_api/detector.py) | [Drift/eventing](../docs/drift-eventing.md) |
| A/B API | Machine request → version-attributed model response | [app](ab_api/app.py), [runtime](ab_api/runtime.py) | [A/B deployment](../docs/ci-ab-deployment.md) |
| MCP tools | Authenticated typed tool call → native feature/model/reference/drift facts | [factory](mcp_tools/app.py), [contracts](mcp_tools/contracts.py), [runtime](mcp_tools/runtime.py) | [MCP](../docs/mcp-tools.md), [application design](../docs/mcp-application-design.md) |

Helm, images, dependency pins and gateway policy are under [infra](../infra/). Tests use controlled adapters at external boundaries; separate native recordings establish deployed behavior. See the [command index](../scripts/README.md) and [operations runbook](../docs/operations.md) for execution requirements.
