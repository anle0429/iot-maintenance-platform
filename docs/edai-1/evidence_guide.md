# Recorded evidence

For a review on GitHub, start with the [recorded results table](../../README.md#recorded-results). Each component report explains what ran and links to its screenshots and selected results. Some reports also point to raw files that become available in a local clone after you restore the receipt bundle.

| Content | Location | How to inspect |
|---|---|---|
| Screenshots, diagrams, report HTML and directly linked results | [Expanded evidence](../evidence/), [figures](../images/) | Open from the relevant component report |
| Original capsule manifests and archive receipts | Each evidence directory | Compare recorded paths, byte counts and SHA-256 values |
| Original native gzip archives | Their existing capsule directories | Download the original archive; its recorded bytes remain unchanged |
| Archived raw logs, source snapshots and machine-readable receipts | [Receipt bundle](../evidence-archive/recorded-runs.tar.gz), [index](../evidence-archive/manifest.json) | Restore locally to open these files or run the verifiers |

## Restore original receipts

```powershell
python -B -S scripts/unpack-evidence.py
python -B -S scripts/verify-learner-demo.py
```

The bundle contains **6,556 original files**, totaling **74,158,342 uncompressed bytes**, in an **8,559,324-byte** gzip archive. Every selected payload was compared with its exact Git blob at `527d63a35ee2c9bf3e78ff3bf034d877e554f69d` before packaging. The original per-capsule inventories and proof assertions were retained.

The restore command checks the pinned index, complete archive, safe paths and every payload hash before creating missing files under `docs/evidence/`. Those expanded files are ignored by Git. Existing matching files are reused; changed local evidence causes a failure and is never overwritten. Running it again is idempotent. `--verify-only` checks without writing missing receipts.

The full audit also compares all 6,556 bundled payloads with their original Git blobs, then restores the bundle automatically:

```powershell
$tag = Get-Date -Format 'yyyyMMdd-HHmmss'
python -B -S scripts/audit-final-coursework.py --output "outputs/audit-$tag"
```

This checks the recorded evidence, the Git history mapping and the 117-row requirement map. The audit runs offline against the captured results. [Live lab verification](operations.md)

Images and native measurements were not edited. Failed predecessor tasks, censored observations, wrong model answers, mixed startup timings and trace warnings remain part of the evidence. Runtime credentials, Terraform state, datasets, weights and personal learning notes are excluded from the submission.

Original source commit identities are resolved through the authenticated [history migration map](history_rewrite.md). The recorded receipt and payload bytes remain unchanged.
