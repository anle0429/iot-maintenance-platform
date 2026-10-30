# Kafka/Flink event-time processing and replay

The native small-source stream path replays telemetry in arrival order through Kafka 3.9.1 and PyFlink 1.20.3. It computes ten-minute event-time windows, routes closed-window arrivals to a late side output and writes absolute aggregates to a retained SQLite sink.

Two source passes delivered 116,928 messages. The result retained 57,600 unique readings and rejected 59,328 duplicates; all 5,760 windows matched an independent oracle. The recorded validation contains 55 unit tests and 10 integration checks, with actual Flink UI captures.

A fifteen-minute watermark delay and zero allowed lateness define when a window becomes final. The processing-time cache can expire while the scenario-lifetime event ledger still prevents old IDs from being counted again. Cache TTL is not a complete ledger-retention or crash-recovery design.

This original workload predates the Feast push integration. Later small CI and full-cohort store checks reuse retained outputs with their own receipts. A successful replay does not prove exactly-once behavior for every failure or an indefinitely running live stream.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
./scripts/run-day3.ps1 -RunName practice3
Get-Content outputs/practice3/stream-report.json
```

## Implementation and evidence

- [Source: pipelines/stream_verification.py](../pipelines/stream_verification.py)
- [Source: pipelines/stream_job.py](../pipelines/stream_job.py)
- [Recorded artifact: day3/native-ttl-failure.txt](evidence/day3/native-ttl-failure.txt)
- [Source: pipelines/stream_contract.py](../pipelines/stream_contract.py)
- [Recorded artifact: day3/finalization-diagnostic.json](evidence/day3/finalization-diagnostic.json)
- [Recorded artifact: day3/stream-report.json](evidence/day3/stream-report.json)
- [Recorded artifact: day3/commands.json](evidence/day3/commands.json)
- [Recorded artifact: day3/unit-tests.txt](evidence/day3/unit-tests.txt)
- [Recorded artifact: day3/run-context.json](evidence/day3/run-context.json)
- [Recorded artifact: day3/final-verification.json](evidence/day3/final-verification.json)
- [Recorded artifact: day3/generated-jvm.txt](evidence/day3/generated-jvm.txt)
- [Source: infra/flink/Dockerfile](../infra/flink/Dockerfile)
- [Source: infra/streaming.compose.yaml](../infra/streaming.compose.yaml)
- [Recorded artifact: day3/flink-python-packages.txt](evidence/day3/flink-python-packages.txt)
- [Recorded artifact: day3/java-version.txt](evidence/day3/java-version.txt)
- [nightlies.apache.org / installation](https://nightlies.apache.org/flink/flink-docs-release-1.20/docs/dev/python/installation/)
- [nightlies.apache.org / kafka](https://nightlies.apache.org/flink/flink-docs-release-1.20/docs/connectors/datastream/kafka/)
- [nightlies.apache.org / windows](https://nightlies.apache.org/flink/flink-docs-release-1.20/docs/dev/datastream/operators/windows/)
- [kafka.apache.org / docker](https://kafka.apache.org/39/getting-started/docker/)
- [nightlies.apache.org / state_backends](https://nightlies.apache.org/flink/flink-docs-release-1.20/docs/ops/state/state_backends/)

## Recorded interface views

These are original captures from the runs described above. Their timestamps, model identities and visible states belong to those runs.

![Recorded ui generated](evidence/day3/ui-generated.jpg)

![Recorded ui generated checkpoints](evidence/day3/ui-generated-checkpoints.jpg)


## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/day3-streaming.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
