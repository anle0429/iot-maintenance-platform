# Large-source preparation and resource budgeting

The large run is admitted only after small and intermediate sources pass physical, transport and checksum validation. The source-generation target was met with 102,447,847,856 actual offline bytes, using continuous state and measured JSONL file sizes.

Before execution, budget source data, Bronze/Silver output, temporary/shuffle files, logs and a safety reserve. HDD capacity and SSD scratch are distinct constraints. A plan that fits the compressed output can still fail while retaining source and shuffle simultaneously.

The runner uses actual free disk/RAM, measured pilot throughput and the remaining authorized VM session. It refuses unsafe extrapolated plans. Immutable receipts bind configuration, machine, source code, checksums and completed output; source generation and Spark ingestion are separate milestones.

The 100 GB measurement is uncompressed offline JSONL. Bronze/Silver full-scale processing is documented in its own report. Gold/features/training are measured on separate smaller sources. Do not restart a source, discard failed evidence or count projected bytes as generated data.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```bash
python3 -m venv data/runtime/scale-venv
data/runtime/scale-venv/bin/python -m pip install -r requirements-scale.txt
data/runtime/scale-venv/bin/python -m unittest discover -s tests -q
```

```bash
data/runtime/scale-venv/bin/python scripts/run-scale-source.py \
  --run-name my-scale-pilot --config generator/configs/scale-calibration.json --fast-json
```

```bash
data/runtime/scale-venv/bin/python - <<'PY'
import hashlib, json
from pathlib import Path
p = Path('outputs/scale-100gb-26')
raw = (p / 'verification.json').read_bytes()
v = json.loads(raw)
receipt = json.loads((p / 'receipt.json').read_text())
q = receipt['quality']
marker = json.loads((p / '_RUN_SUCCESS.json').read_text())
assert marker['verification_sha256'] == hashlib.sha256(raw).hexdigest()
assert v['passed'] and q['passed'] and all(q['checks'].values())
assert v['source_manifest_sha256'] == receipt['source_manifest_sha256']
assert v['actual_offline_payload_bytes'] == q['offline_payload_bytes']
assert v['actual_offline_payload_bytes'] >= 100_000_000_000
print(v['actual_offline_payload_bytes'], v['unique_rows'], len(q['checks']))
PY
```

```bash
data/runtime/scale-venv/bin/python scripts/plan-large-ingest.py \
  --deadline-utc "$VM_STOP_UTC" --output outputs/my-large-ingest-plan.json
```

## Implementation and evidence

- [Recorded artifact: scale-source/verification.json](evidence/scale-source/verification.json)
- [Recorded artifact: scale-source/scale-100gb-26/verification.json](evidence/scale-source/scale-100gb-26/verification.json)
- [Recorded artifact: scale-source/systemd-limits.txt](evidence/scale-source/systemd-limits.txt)
- [github.com / orjson](https://github.com/ijl/orjson)
- [Recorded artifact: scale-source/calibration-comparison.json](evidence/scale-source/calibration-comparison.json)
- [Recorded artifact: scale-source/calibration-runtime-v1/pipelines__source_runtime.py.txt](evidence/scale-source/calibration-runtime-v1/pipelines__source_runtime.py.txt)
- [Recorded artifact: scale-source/calibration-runtime-v1/scripts__run-scale-source.py.txt](evidence/scale-source/calibration-runtime-v1/scripts__run-scale-source.py.txt)
- [Recorded artifact: scale-source/runtime.json](evidence/scale-source/runtime.json)
- [Recorded artifact: scale-source/scale-intermediate-26/receipt.json](evidence/scale-source/scale-intermediate-26/receipt.json)
- [Recorded artifact: scale-source/scale-intermediate-26/verification.json](evidence/scale-source/scale-intermediate-26/verification.json)
- [Recorded artifact: scale-source/intermediate-spark/ingest-report.json](evidence/scale-source/intermediate-spark/ingest-report.json)
- [Recorded artifact: scale-source/intermediate-spark/spark-metrics.json](evidence/scale-source/intermediate-spark/spark-metrics.json)
- [Recorded artifact: scale-source/intermediate-spark/sampled-resources.json](evidence/scale-source/intermediate-spark/sampled-resources.json)
- [Recorded artifact: scale-source/intermediate-spark/unit-tests.txt](evidence/scale-source/intermediate-spark/unit-tests.txt)
- [Recorded artifact: gcp-runtime/runtime-source-v3-manifest.json](evidence/gcp-runtime/runtime-source-v3-manifest.json)
- [Recorded artifact: scale-source/git-source-mapping.json](evidence/scale-source/git-source-mapping.json)
- [Recorded artifact: scale-source/vm-restart/rejected-old-session-plan.json](evidence/scale-source/vm-restart/rejected-old-session-plan.json)
- [Recorded artifact: scale-source/vm-restart/before-restart.json](evidence/scale-source/vm-restart/before-restart.json)
- [Recorded artifact: scale-source/vm-restart/after-restart.json](evidence/scale-source/vm-restart/after-restart.json)
- [Recorded artifact: scale-source/fresh-session/source-after-restart.json](evidence/scale-source/fresh-session/source-after-restart.json)
- [Recorded artifact: scale-source/vm-restart/sessions.json](evidence/scale-source/vm-restart/sessions.json)
- [Recorded artifact: scale-source/fresh-session/100gb-plan.json](evidence/scale-source/fresh-session/100gb-plan.json)
- [Source: generator/configs/scale-100gb.json](../generator/configs/scale-100gb.json)
- [Recorded artifact: scale-source/scale-100gb-26/receipt.json](evidence/scale-source/scale-100gb-26/receipt.json)
- [Recorded artifact: scale-source/scale-100gb-26/source-inventory.json](evidence/scale-source/scale-100gb-26/source-inventory.json)
- [Recorded artifact: scale-source/scale-100gb-26/source-files.jsonl](evidence/scale-source/scale-100gb-26/source-files.jsonl)
- [Recorded artifact: scale-source/fresh-session/_PROTOCOL_SUCCESS.json](evidence/scale-source/fresh-session/_PROTOCOL_SUCCESS.json)
- [Recorded artifact: scale-source/final-health.json](evidence/scale-source/final-health.json)
- [Recorded artifact: scale-source/final-services.json](evidence/scale-source/final-services.json)
- [Recorded artifact: scale-source/unit-after.txt](evidence/scale-source/unit-after.txt)
- [Source: pipelines/features.py](../pipelines/features.py)
- [Source: pipelines/orchestrated_job.py](../pipelines/orchestrated_job.py)
- [Recorded artifact: large-ingest-runner/admission/plan.json](evidence/large-ingest-runner/admission/plan.json)
- [Recorded artifact: large-ingest-plan/plan.json](evidence/large-ingest-plan/plan.json)
- [Source: pipelines/ingest_budget.py](../pipelines/ingest_budget.py)
- [Recorded artifact: large-ingest-plan/checks.json](evidence/large-ingest-plan/checks.json)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/scale-100gb.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
