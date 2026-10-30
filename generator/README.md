# Pump source generator

The generator separates simulated physical history from data delivery. Wear, load, failures and maintenance determine sensor values and operating state. Export retries, duplicates, schema changes, skew and late arrival are separate transport/data-quality effects.

| Boundary | Implementation |
|---|---|
| Configuration and seed | [config.py](config.py), [development config](configs/dev.json) |
| Machine history and sensor simulation | [simulation.py](simulation.py) |
| Chunked generation and measured resource planning | [chunked.py](chunked.py), [chunked-generator report](../docs/chunked-generator.md) |
| Persisted-output checks | [quality.py](quality.py), [data contract](../docs/data-contract.md) |
| Separate sample/target tables and controlled drift | [Assistant data validation](../docs/assistant-data-validation.md) |

From the project root, Python 3.12/3.13 is sufficient for a small run:

```powershell
$run = "local-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
python -S -m generator generate --config generator/configs/dev.json --output "data/source/$run"
python -S -m generator inspect --source "data/source/$run" --report "outputs/$run/quality.json"
```

The development config uses 20 machines over 48 hours. Use new output names to preserve earlier runs. Small runs demonstrate data contracts; [100 GB ingestion](../docs/full-scale-ingest.md) has separate measured source/readback evidence and resource guards. The model's larger training set uses its own [60-day protocol](../docs/ml-evaluation-protocol.md).
