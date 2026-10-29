# DataHub restoration after a VM restart

The restoration procedure preserves the existing DataHub images, metadata volumes and indexed entities after STOP/START. Native readbacks confirm the two feature datasets, producing job and six existing assertions remain available.

The UI and GMS checks use the restored service and its actual dependencies. They do not regenerate lineage, rerun materialization or republish metadata merely to make the interface look populated.

The receipt and original captures establish same-state restoration for this lab. UI availability alone does not prove every downstream pipeline is healthy; review the specific entity/assertion payloads and their producer identities.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
python -S scripts/verify-datahub-restore.py
```

## Implementation and evidence

- [Recorded artifact: datahub-restore-linux/restore-v2/verification.json](evidence/datahub-restore-linux/restore-v2/verification.json)
- [Recorded artifact: datahub-restore-linux/before.json](evidence/datahub-restore-linux/before.json)
- [Recorded artifact: datahub-restore-linux/restore-v2/progress.json](evidence/datahub-restore-linux/restore-v2/progress.json)
- [Recorded artifact: datahub-restore-linux/config-parity.json](evidence/datahub-restore-linux/config-parity.json)
- [Recorded artifact: datahub-restore-linux/indexed/verification.json](evidence/datahub-restore-linux/indexed/verification.json)
- [Recorded artifact: datahub-restore-linux/indexed/input.json](evidence/datahub-restore-linux/indexed/input.json)
- [Recorded artifact: datahub-restore-linux/indexed/output.json](evidence/datahub-restore-linux/indexed/output.json)
- [Recorded artifact: datahub-restore-linux/manifest.json](evidence/datahub-restore-linux/manifest.json)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](demo-walkthrough.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/datahub-restore.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
