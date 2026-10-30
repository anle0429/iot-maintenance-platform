# Chunked generation with continuous physical state

The bounded writer carries each machine's simulation state across output parts. A chunk boundary must not restart wear, maintenance, random streams or failure history. Rows keep the same grain, units and timestamps as the source contract.

The writer creates limited-size JSONL parts and a catalog with measured byte counts and checksums. Transport selection uses exact counts over the full run, with a bounded arrival heap for streaming delivery order. Selected duplicate IDs can differ from the legacy writer's transport algorithm; versions must not be mixed as one source run.

The validator streams the catalog and parts, checks the complete event grid, intervals, exact duplicate/late rates and bounded joins between deliveries and telemetry. Known-answer boundary and byte-reproducibility checks precede larger pilots.

Capacity planning budgets source, retained output, shuffle, logs and reserve separately. Intermediate runs measure throughput and peak state. The later 102.45 GB source and full Spark readback have their own actual receipts; extrapolation alone is never accepted as completion.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
./scripts/run-chunked.ps1 -RunName practice-chunks
Get-Content outputs/practice-chunks/chunked-report.json
```

```powershell
./.venv/Scripts/python.exe -m generator.chunked generate --config generator/configs/chunked-pilot.json --output data/source/manual-chunks --chunk-rows 4096
./.venv/Scripts/python.exe -m generator.chunked inspect --source data/source/manual-chunks --report outputs/manual-chunks-quality.json
./.venv/Scripts/python.exe -m generator.chunked plan --source data/source/manual-chunks --target-offline-gb 100 --offline-only --report outputs/manual-chunks-plan.json
```

## Implementation and evidence

- [Source: generator/simulation.py](../generator/simulation.py)
- [Source: generator/chunked.py](../generator/chunked.py)
- [Source: generator/chunk_quality.py](../generator/chunk_quality.py)
- [Source: generator/capacity.py](../generator/capacity.py)
- [Recorded artifact: chunked/chunked-report.json](evidence/chunked/chunked-report.json)
- [Recorded artifact: chunked/pilot-quality.json](evidence/chunked/pilot-quality.json)
- [Recorded artifact: chunked/scale-quality.json](evidence/chunked/scale-quality.json)
- [Recorded artifact: chunked/repeat-comparison.json](evidence/chunked/repeat-comparison.json)
- [Recorded artifact: chunked/different-chunks-comparison.json](evidence/chunked/different-chunks-comparison.json)
- [Recorded artifact: chunked/legacy-regression.json](evidence/chunked/legacy-regression.json)
- [Recorded artifact: chunked/unit-tests.txt](evidence/chunked/unit-tests.txt)
- [Recorded artifact: chunked/final-verification.json](evidence/chunked/final-verification.json)
- [Recorded artifact: chunked/100gb-offline-only-plan.json](evidence/chunked/100gb-offline-only-plan.json)
- [Recorded artifact: chunked/100gb-with-stream-plan.json](evidence/chunked/100gb-with-stream-plan.json)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/chunked-generator.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
