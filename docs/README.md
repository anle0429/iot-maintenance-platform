# IoT Maintenance Platform documentation

The platform uses simulated pump data to estimate whether a running pump will fail within the next hour. A maintenance assistant can also look up the pump's sensor summaries, request its score and find relevant maintenance passages.

## Follow one pump through the system

Suppose a technician asks about pump `P-001` at 10:00. The training target is a failure beginning after 10:00 and by 11:00. For a prediction request, the API checks the prepared operating state and the age of the sensor inputs before sending them to the model. If the technician asks in chat, the assistant uses tool calls to get the values and looks up the relevant maintenance passages. [Data contract](edai-1/data_contract.md)

| Question | Where to look | What you can verify |
|---|---|---|
| Where does a reading end up? | [Data Platform viewer](diagrams/README.md#current-platform-diagrams) · [Batch and stream jobs](edai-1/processing_jobs.md) | Spark writes batch tables to Parquet. Flink writes 10-minute aggregates and audit records to SQLite. |
| How does the model learn, and who decides whether to score a pump? | [Training](edai-1/training_notebook.md) · [Feature API](edai-1/feature_api.md) | The 60-day training run uses earlier observations to predict later failures. In the serving test, the API scored 195 pumps and returned HTTP 409 for five stopped pumps. |
| Where does the assistant get its answer? | [Agent workflow](edai-2/llm_platform.md) · [Recorded conversation](edai-2/agent_runtime_validation.md) | The coordinator delegates to specialists. The saved conversation shows the tool calls, the returned facts and the final answer. |
| What does monitoring measure? | [Current monitoring and scaling](architecture/ml-llm-platform-v19-notes.md) | Offline drift compares feature distributions with a saved training reference. Separate Prometheus metrics measure demand for Search and Drift MCP. |

The 102.45 GB ingest run, the 60-day training set and the retained streaming replay are separate workloads. The evaluation and API scores come from simulated equipment. [Recorded results](../README.md#recorded-results) · [Evidence guide](edai-1/evidence_guide.md)

## Browse by component

| Part | Guide |
|---|---|
| Data generation, processing, storage, training and prediction | [Data and ML](edai-1/README.md) |
| Document search, agent calls, GPU inference and observability | [LLM and agents](edai-2/README.md) |
| Current system views, individual flows and deployment | [Architecture diagram index](diagrams/README.md) |
| Local verification and live lab setup | [Command index](../scripts/README.md) · [Operations](edai-1/operations.md) |

```text
docs/
├── README.md
├── edai-1/
│   ├── README.md
│   ├── docker.md
│   ├── data_generator.md
│   ├── processing_jobs.md
│   ├── data_storage.md
│   └── ...
├── edai-2/
│   ├── README.md
│   ├── llm_inference_platform.md
│   ├── global_model_config.md
│   ├── observability.md
│   └── ...
├── architecture/          Current platform diagrams and previous revisions
├── diagrams/              Diagram index, focused views and execution guide
├── images/
├── evidence/
├── evidence-archive/
├── rubric/
└── navigation.json
```

## Review and evidence

- See the changes made after mentor feedback and the results recorded for each review. [Mentor review follow-up](mentor_review_followup.md) · [Data review checklist](edai-1/data_review_audit.md)
- Find the implementation and evidence for each requirement. [Requirement map](edai-1/implementation_map.md) · [ML tracker](edai-1/rubric_tracker.md) · [LLM tracker](edai-2/rubric_tracker.md)

The diagrams explain the design. The linked reports identify the run, input data and measured result. Some raw receipts are stored in an archive. To follow those links locally, run `python -B -S scripts/unpack-evidence.py` from the repository root. The full offline audit restores that bundle automatically. [Evidence storage and restore](edai-1/evidence_guide.md)
