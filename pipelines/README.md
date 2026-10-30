# Batch and stream processing

The data platform publishes validated outputs between processing stages. A downstream job reads those persisted datasets or finalized windows rather than assuming another process is still running.

| Flow | Input → output | Entry / report |
|---|---|---|
| DP1 ingestion | Exported pump records → Bronze | [Chunk ingestion](chunk_source.py), [full-scale processing](../docs/full-scale-ingest.md) |
| DP2 validation and aggregation | Bronze → cleaned Silver and machine/time aggregates | [Batch contract](orchestration_contract.py), [Airflow stages](../docs/orchestration.md) |
| DP3 feature/label preparation | Eligible arrived history → features and separately governed targets | [Bounded features](bounded_features.py), [evaluation protocol](../docs/ml-evaluation-protocol.md) |
| Streaming | Arrival-ordered deliveries → deduplicated event-time windows | [Streaming design](../docs/streaming.md), [window cohort](../docs/window-cohort.md) |
| Feature-store delivery | Finalized windows → offline history / online latest value | [Stream CI](../docs/ci-stream-push.md), [store replay](../docs/stream-feast-push.md) |
| Governance | Pipeline outputs / validation → DataHub schemas, assertions and lineage | [Orchestration](../docs/orchestration.md) |

The [data contract](../docs/data-contract.md) defines grains and clocks. The [training protocol](../docs/ml-evaluation-protocol.md) defines eligibility and censoring. Container/Spark commands, measured workload sizes and resource requirements belong to their specific reports; the local generator example does not reproduce a large Spark run.
