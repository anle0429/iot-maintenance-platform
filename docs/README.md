# Technical documentation

The first part follows telemetry through processing, storage, training and serving. The second follows requests through model inference and the agent platform. The main guides below collect implementation code and recorded results by topic.

| Part | Scope | Guide |
|---|---|---|
| EDAI 1 | Generation, batch/stream processing, orchestration, feature stores, training, serving and operations | [Data and ML](edai-1/README.md) |
| EDAI 2 | Model inference/configuration, RAG, MCP, agents, evaluation, security and observability | [LLM and agents](edai-2/README.md) |

## Main review guides

| Topic | Code, results and reproduction |
|---|---|
| Batch and stream processing | [Processing jobs](edai-1/processing_jobs.md) |
| Storage and feature stores | [Data storage](edai-1/data_storage.md) |
| Inference setup and verification | [LLM inference platform](edai-2/llm_inference_platform.md) |
| MCP and A2A gateway, model settings and tool rollout | [Agentgateway integration](edai-2/global_model_config.md) |
| Worker lifecycle and startup | [Agent warm-up](edai-2/agent_warmup.md) |

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
├── diagrams/
├── images/
├── evidence/
├── evidence-archive/
├── rubric/
└── navigation.json
```

## Review and evidence

- [Architecture diagrams](diagrams/README.md): the system map, C4 feature-access view, five dynamic views and physical deployment, with source references and zoom controls.
- [Mentor review follow-up](mentor_review_followup.md): the latest documentation changes, scoped measurements and remaining native acceptance work.
- [Data review checklist](edai-1/data_review_audit.md): eleven reported issues mapped to code, explanation and visible results.
- [Requirement map](edai-1/implementation_map.md): 117 final requirements, with separate [ML](edai-1/rubric_tracker.md) and [LLM](edai-2/rubric_tracker.md) trackers.
- [Evidence guide](edai-1/evidence_guide.md): original screenshots, native receipts, archived payloads and verification boundaries.
- [Operations](edai-1/operations.md): private lab access, recovery and live demonstration prerequisites.

Pages identify what ran, which source/result identities were checked and where measurements stop. Diagrams are illustrations; screenshots retain their original run identities. Failed attempts and mixed results remain visible.

Run examples from the repository root. A clone contains no credentials, bulk data, service state or model weights. Individual offline verifiers first need `python -B -S scripts/unpack-evidence.py`; the full artifact audit restores that bundle automatically. [Navigation metadata](navigation.json) preserves traceability after moving the pages.
