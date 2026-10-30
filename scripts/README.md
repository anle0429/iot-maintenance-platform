# Command entry points

Run commands from the project root. Library code lives in the domain packages; these files expose bounded generation, deployment, operation or verification commands. Existing entry-point paths remain stable because CI releases and historical source seals refer to them.

| Task | Entry point | Prerequisite / output |
|---|---|---|
| Generate and inspect a small source | `python -S -m generator generate`, `python -S -m generator inspect` | [Generator guide](../generator/README.md); writes local `data/` and `outputs/` |
| Plan/run large ingestion | [plan-large-ingest.py](plan-large-ingest.py), [run-large-ingest.py](run-large-ingest.py) | [Measured resource gates](../docs/scale-100gb.md); unique run and actual session deadline |
| Run data pipelines | [start-orchestration.py](start-orchestration.py) | [Airflow runbook](../docs/orchestration.md); retained images and service state |
| Train and publish model releases | [run-notebook-kfp.py](run-notebook-kfp.py), [ci-training.py](ci-training.py), [ci-serving.py](ci-serving.py) | Fixed protocol, sealed source, native runtime and release-specific outputs |
| Recover CPU/GPU services | [resume-demo-services.py](resume-demo-services.py), [resume-gpu-services.py](resume-gpu-services.py) | [Operations](../docs/operations.md); actual Compute descriptors and existing private state |
| Prepare genuine feature data | [refresh-agent-features.py](refresh-agent-features.py) | Expired physical cache, retained SDK source/model bundle and sufficient approved session |
| Check a complete agent answer | [run-agent-demo.py](run-agent-demo.py) | Actual ready route/pools/MCP replicas and an unexpired data lease |
| Apply the measured monitoring budget | [configure-llm-steady-state.py](configure-llm-steady-state.py) | Existing deployment/configuration baseline; thirteen health observations |
| Open private administrative views | [view-agent-gateway.py](view-agent-gateway.py), [view-llm-monitoring.py](view-llm-monitoring.py), [view-vault-metadata.py](view-vault-metadata.py) | Private CA, scoped existing credentials, loopback binding and IAP |
| Restore archived receipts | [unpack-evidence.py](unpack-evidence.py) | Python standard library; no VM |
| Audit all recorded requirements | [audit-final-coursework.py](audit-final-coursework.py) | Python/Git; restores original evidence automatically |
| Check English docs, links and figures | [check-documentation.py](check-documentation.py) | Run after restoring evidence; no platform service |

`verify-*.py` files check recorded artifacts unless their own CLI explicitly describes a live request. Their historical test counts are scoped to their original source and workload. Build/deploy/CI commands require the environment described in the linked runbook; they are not interchangeable with offline verification.
