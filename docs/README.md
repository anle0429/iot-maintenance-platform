# IoT Maintenance Platform documentation

## Architecture

- [Data Platform viewer](https://htmlpreview.github.io/?https://github.com/anle0429/iot-maintenance-platform/blob/main/docs/architecture/data-platform-v16.html)
- [ML & LLM Platform viewer](https://htmlpreview.github.io/?https://github.com/anle0429/iot-maintenance-platform/blob/main/docs/architecture/ml-llm-platform-v19.html)
- [Architecture diagram index](diagrams/README.md)

## Components

| Component | Documentation |
|---|---|
| Data and ML | [Component index](edai-1/README.md) · [Data contract](edai-1/data_contract.md) · [Processing jobs](edai-1/processing_jobs.md) · [Storage and feature stores](edai-1/data_storage.md) |
| Training and serving | [Training](edai-1/training_notebook.md) · [Feature API](edai-1/feature_api.md) · [Serving release](edai-1/ci_serving.md) |
| LLM and agents | [Component index](edai-2/README.md) · [Agent workflow](edai-2/llm_platform.md) · [Document indexing](edai-2/rag_orchestration.md) |
| Monitoring | [Offline drift and scaling](architecture/ml-llm-platform-v19-notes.md) · [Observability](edai-2/observability.md) |

## Operations

- [Lab setup and operations](edai-1/operations.md)
- [Command index](../scripts/README.md)

## Results and evidence

- [Recorded results](../README.md#recorded-results) · [Agent runtime validation](edai-2/agent_runtime_validation.md) · [Evidence guide](edai-1/evidence_guide.md)
- [Requirement map](edai-1/implementation_map.md) · [ML tracker](edai-1/rubric_tracker.md) · [LLM tracker](edai-2/rubric_tracker.md)
- [Mentor review follow-up](mentor_review_followup.md) · [Data review checklist](edai-1/data_review_audit.md)

To open archived receipts locally, run `python -B -S scripts/unpack-evidence.py` from the repository root. The offline audit restores the bundle automatically.
