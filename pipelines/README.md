# Batch and stream processing

The data platform publishes validated outputs between processing stages. A downstream job reads those persisted datasets or finalized windows rather than assuming another process is still running.

| Flow | Input → output | Entry / report |
|---|---|---|
| DP1 ingestion | Exported pump records → Bronze | [Chunk ingestion](chunk_source.py), [full-scale processing](../docs/edai-1/full_scale_ingest.md) |
| DP2 validation and aggregation | Bronze → cleaned Silver and machine/time aggregates | [Batch contract](orchestration_contract.py), [Airflow stages](../docs/edai-1/orchestration.md) |
| DP3 feature/label preparation | Eligible arrived history → features and separately governed targets | [Bounded features](bounded_features.py), [evaluation protocol](../docs/edai-1/ml_evaluation_protocol.md) |
| Streaming | Arrival-ordered deliveries → deduplicated event-time windows | [Streaming design](../docs/edai-1/streaming.md), [window cohort](../docs/edai-1/window_cohort.md) |
| Feature-store delivery | Finalized windows → offline history / online latest value | [Stream CI](../docs/edai-1/ci_stream_push.md), [store replay](../docs/edai-1/stream_feast_push.md) |
| Governance | Pipeline outputs / validation → DataHub schemas, assertions and lineage | [Orchestration](../docs/edai-1/orchestration.md) |

The [data contract](../docs/edai-1/data_contract.md) defines grains and clocks. The [training protocol](../docs/edai-1/ml_evaluation_protocol.md) defines eligibility and censoring. Container/Spark commands, measured workload sizes and resource requirements belong to their specific reports; the local generator example does not reproduce a large Spark run.
