# Recorded evidence

The submission keeps reports readable on GitHub and the underlying receipts independently checkable. Packaging changes how historical files are stored, not what their contents claim.

| Content | Location | How to inspect |
|---|---|---|
| Screenshots, diagrams, report HTML and directly linked results | [Expanded evidence](evidence/), [figures](images/) | Open from the relevant component report |
| Original capsule manifests and archive receipts | Each evidence directory | Compare recorded paths, byte counts and SHA-256 values |
| Original native gzip archives | Their existing capsule directories | Download the original archive; its recorded bytes remain unchanged |
| Historical logs, source snapshots and machine-readable payloads without direct documentation links | [Receipt bundle](evidence-archive/recorded-runs.tar.gz), [index](evidence-archive/manifest.json) | Restore locally, then use the existing independent verifiers |

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

This runs the same 59 recorded-evidence checks and the 117-row requirement map. It starts no platform service and sends no model request. A successful offline check establishes the captured result and its scope, not current endpoint readiness or a mentor-awarded grade.

Images and native measurements were not edited. Failed predecessor tasks, censored observations, wrong model answers, mixed startup timings and trace warnings remain part of the evidence. Runtime credentials, Terraform state, datasets, weights and personal learning notes are excluded from the submission.
