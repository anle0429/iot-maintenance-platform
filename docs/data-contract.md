# Source data contract

## Observation and storage

One telemetry record describes one simulated pump at one measurement time. Re-delivery and re-export preserve `event_id`. Timestamps use UTC ISO 8601 with second precision. Observation intervals are `[start_ts, end_ts)`; the default small run contains 20 machines over 48 hours at one-minute intervals, or 57,600 unique readings.

Source JSONL is UTF-8, uncompressed and LF-terminated. Its manifest records the configuration, simulator version, row counts, SHA-256 and measured bytes. Source directories are immutable after generation. The small in-memory generator has a 100,000-unique-event guard; the chunked writer is a separate bounded implementation.

## Entities and timestamps

| Dataset | Grain and key | Meaning |
|---|---|---|
| `machines` | One metadata snapshot per `machine_id` | Factory, machine type, rated RPM and validity fields; later metadata changes form SCD2 history |
| `maintenance_events` | One interval per `maintenance_id` | Preventive maintenance and downtime |
| `failure_events` | One failure onset per `failure_id` | Wear-triggered failure and repair interval |
| Telemetry v1/v2 | One exported reading per `event_id`, with intentional source duplicates | Temperature, vibration, current, RPM and observed status; v2 adds pressure |
| Stream deliveries | One delivery per `delivery_id` | Event payload, attempt and arrival-order envelope |

Telemetry carries `event_id`, `machine_id`, `event_ts`, `received_ts`, `created_ts`, `schema_version` and `operating_status`. Numeric measures must be finite. `event_ts <= received_ts <= created_ts`. `received_ts` is first arrival and remains unchanged on retries. `created_ts` identifies an export; it must not replace first availability.

Status is `operating`, `maintenance` or `failed`. Stopped motors have zero current and RPM. Firmware evolution follows event time: a late v1 record remains v1. Missing v1 pressure is NULL after schema union, not an invented zero. Telemetry excludes latent wear, future failure timestamps and ML labels.

## Physical and transport simulation

Load, machine-specific wear and noise influence the sensor values. Crossing a wear threshold creates a failure; labels are not sampled independently. Default preventive maintenance occurs every 18 hours with per-machine offsets, lasts 20 minutes and reduces wear to 35%. Failures stop the motor for 45 minutes. Downtime intervals are `[start, end)`. An unobserved recovery/end remains NULL.

Default transport problems are 70% of unique readings at factory F-01, 2% additional offline exports, 1.5% additional stream retries, and 5% late unique readings delayed 300–600 seconds. Each ratio uses unique readings as its denominator. The validator also reports the fraction of stored rows that are extras. Separate random streams preserve physical history when transport settings change.

## Processing and prediction rules

Batch deduplication keeps the latest `created_ts` for each event ID and rejects conflicting immutable payloads. Flink uses ten-minute tumbling windows, a fifteen-minute watermark delay and zero allowed lateness. A reading for a closed window goes to a late side output. The seven-day processing-time cache and the scenario-lifetime event ledger serve different retention purposes; they do not prove arbitrary crash recovery.

For an operating machine at t, `failure_in_next_1h=1` only if a failure starts in `(t, t+1h]`. Insufficient observation, stopped machines and censored horizons are not assigned a negative label. Feature inputs must satisfy both event-time and arrival/recording-time availability. Future failure history is used only for labels. Time-based train/validation/test boundaries purge overlapping label windows.

The chunked layout preserves these meanings while writing bounded parts and a checksum catalog. Its ordering is a generator contract, not a general assumption about all IoT sources. The ingest adapter verifies receipt, catalog and part hashes before Spark reads the offline tables.

## Implementation and evidence

- [docs.python.org / random.html](https://docs.python.org/3.13/library/random.html)
- [Source: pipelines/chunk_source.py](../pipelines/chunk_source.py)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](demo-walkthrough.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/data-contract.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
