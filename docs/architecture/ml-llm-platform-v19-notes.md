# ML and LLM Platform v19

Use **Training & release** for the offline PSI inputs and **Observability & scaling** for the two Prometheus scrape sources. [Interactive viewer](https://htmlpreview.github.io/?https://github.com/anle0429/iot-maintenance-platform/blob/main/docs/architecture/ml-llm-platform-v19.html) · [Editable Excalidraw](ml-llm-platform-v19.excalidraw)

## Offline drift

The `iot_offline_drift` Airflow DAG passes two inputs to its offline monitoring job.

| Input | What the job reads |
|---|---|
| O16 · Rolling feature history | A bounded window from `machine_rolling.parquet`. The job retrieves values through Feast and checks them against a DuckDB query. |
| O17 · Training reference | A saved JSON reference built from the training partition. The monitor checks the reference hash and its protocol and partition identities. |

The job compares the eight feature distributions using PSI and publishes the results through Pushgateway. If policy allows, Airflow dispatches a separate candidate DAG that checks the receipt before submitting to Kubeflow. [DAG](../../airflow/dags/iot_offline_drift.py) · [Offline job](../../feature_store/drift_monitor.py) · [Candidate admission](../../ml/drift_retrain.py)

The interactive Drift MCP path checks the currently served model's identity and uses its matching reference. [Drift MCP runtime](../../services/mcp_tools/runtime.py) · [Recorded serving-model drift check](ml-llm-platform-v18-notes.md#cpu-lab-verification-on-november-24-2026)

## Search and drift metrics

| Prometheus job | What it scrapes | What KEDA uses |
|---|---|---|
| O12 · `iot-rag-search` | Search Pod metrics for valid arrivals, active queries and queued work. The arrival counter includes valid requests later rejected with HTTP 429. | The 30-second arrival rate for the RAG Search Deployment. |
| O18 · `iot-agent-mcp` | MCP Pod tool counters. For the drift scaler, the query selects `role="drift"`, `tool="assess_feature_drift"` and `result="success"`. | The 30-second rate of successful drift assessments for the Drift MCP Deployment. |

Retrieval MCP forwards document-search calls to RAG Search API. Each Search Pod verifies and copies the sealed index at startup. Search and Drift MCP have separate scaling targets. [Scrape configuration](../../scripts/render-rag-search-prometheus.py) · [Search metrics](../../services/rag_search/app.py) · [Scaler queries](../../infra/charts/iot-mcp/templates/request-scaling.yaml) · [Search Pod startup](../../infra/charts/iot-mcp/templates/search.yaml)

The earlier v18 release notes retain the native drift, scaling and Prometheus results recorded on November 24, 2026. [Release notes and lab results](ml-llm-platform-v18-notes.md)
