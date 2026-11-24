# ML and LLM Platform v18

This view shows the implementation after the architecture review. The earlier Jenkins serving build is documented in the serving CI report. [Interactive diagram](ml-llm-platform-v18.html) · [Editable diagram](ml-llm-platform-v18.excalidraw) · [Previous deployment evidence](../edai-1/ci_serving.md)

## Feature serving and online drift

The refresh command resolves the existing serving release, stages a new Redis store and seed, verifies the candidate and switches Feature API. This path reuses the active KServe model. [Refresh command](../../scripts/refresh-agent-features.py)

Drift MCP's serving-model path reads each machine's feature trace and serving identity together. It assesses at least 32 operating machines against the matching model's training partition. A cohort that spans model releases or a reference with a different identity is rejected. Historical fleet analysis remains an explicit mode. [Feature boundary](../../services/feature_api/runtime.py) · [Drift MCP](../../services/mcp_tools/runtime.py)

To prepare a reference for that path, run the reference builder with the selected model's verified `--partitions`, `--registration`, `--protocol` and `--serving-pin` plus new `--output` and `--configmap-output` paths. Apply the generated immutable ConfigMap to the Drift API release and select its name as `volumes[0].configMap.name` in the Drift API Helm values. The drift Jenkins job checks the live reference against the model pin before setting the MCP reference ID. [Reference builder](../../scripts/build-drift-reference.py) · [Drift API values](../../infra/drift-api/helm-values.yaml) · [Agent release checks](../../scripts/ci-agents.py)

## Capacity and failure boundaries

Retrieval MCP readiness follows the local serving lifecycle. Each tool propagates its own dependency failure. Release probes still check the private backends. When request-based scaling is enabled, Search arrivals and successful Drift MCP calls drive separate scaling targets. Search bounds its work queue and reports its own demand. [MCP Deployment](../../infra/charts/iot-mcp/templates/workload.yaml) · [RAG Search API](../../services/rag_search/app.py)

Drift API reports new-write capacity separately at `/health/write` and leaves replays routable at the admission limit. The existing Prometheus scrape exposes `iot_drift_assessments_active`, `iot_drift_assessments_active_limit` and `iot_drift_capacity_check_up`. Install the supplied rule file on the monitoring host and pass its path as `--drift-capacity-rules` to the configuration renderer. The rules warn at 80% active capacity or when the ledger check fails. The rule uses the maximum utilization across API replicas sharing the ledger. To reclaim active slots, run `python -m services.drift_api.runtime --keep-active 8000` in the existing writable Drift API runtime. The transaction compresses older responses in the same database and retains their identities and replay results. Disk usage still needs monitoring as the archive grows. [Capacity and archive implementation](../../services/drift_api/runtime.py) · [Prometheus rules](../../infra/observability/drift-capacity.rules.yaml) · [Configuration renderer](../../scripts/render-rag-search-prometheus.py)

## Offline PSI and candidate training

`iot_offline_drift` publishes PSI and dispatches `iot_offline_drift_retrain` when policy allows. The candidate DAG independently checks the bound receipt and submits and verifies KFP. The five-minute monitor can finish while KFP runs. [Airflow DAG](../../airflow/dags/iot_offline_drift.py) · [Candidate controller](../../ml/drift_retrain.py)

## CPU lab verification on November 24, 2026

- Drift MCP accepted 32 operating machines for production model v6. Its reference contained 1,024 rows from that model's training split. Reversing the input order returned the same assessment.
- With the retrieval frontend at two replicas, KEDA grew RAG Search from two ready replicas to four and returned it to two. Two new Search Pods handled requests. The load generated 7,240 successful searches and 3,256 HTTP 429 responses under backpressure, with zero other failures.
- The existing Prometheus server loaded both rules and scraped the real Drift API capacity series. In an isolated Prometheus process on the same VM, both rules fired and cleared at their configured thresholds using controlled series.
