# Batch and stream processing

The Spark stages read and write Parquet tables on the VM disk. Flink consumes replayed Kafka deliveries and writes finalized windows and audit records to SQLite. Downstream publication jobs read the stored results. [Data Platform diagram](../docs/diagrams/README.md#current-platform-diagrams)

| Flow | Input → output | Entry / report |
|---|---|---|
| DP1 ingestion | Exported pump records → Bronze | [Chunk ingestion](chunk_source.py), [full-scale processing](../docs/edai-1/full_scale_ingest.md) |
| DP2 cleaning and modeling | Bronze → deduplicated Silver telemetry and cleaned event tables, plus Gold machine and factory dimensions and telemetry, failure and maintenance facts | [Spark job](orchestrated_job.py), [Airflow stages](../docs/edai-1/orchestration.md) |
| DP3 feature/label preparation | Eligible arrived history → features and separately governed targets | [Bounded features](bounded_features.py), [evaluation protocol](../docs/edai-1/ml_evaluation_protocol.md) |
| Streaming | Arrival-ordered deliveries → deduplicated event-time windows | [Streaming design](../docs/edai-1/streaming.md), [window cohort](../docs/edai-1/window_cohort.md) |
| Feature-store delivery | Finalized windows → offline history / online latest value | [Stream CI](../docs/edai-1/ci_stream_push.md), [store replay](../docs/edai-1/stream_feast_push.md) |
| Governance | Pipeline outputs / validation → DataHub schemas, assertions and lineage | [Orchestration](../docs/edai-1/orchestration.md) |

The [data contract](../docs/edai-1/data_contract.md) defines grains and clocks. The [training protocol](../docs/edai-1/ml_evaluation_protocol.md) defines eligibility and censoring. Container/Spark commands, measured workload sizes and resource requirements belong to their specific reports; the local generator example does not reproduce a large Spark run.
