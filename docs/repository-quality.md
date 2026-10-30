# Repository quality and documentation checks

A frozen full-suite run at `b8f0ef8` passed 732 tests,292 subtests and seven documented skips across 307 Python inputs. Its audit found 1,637 documented definitions. Later independent-clone verification at `3398a3c` passed 748 tests,314 subtests and seven skips, with 325 Git inputs unchanged before/after and 26 evidence verifiers successful.

The earlier documentation repair added 419 docstrings across 75 files. Its original edit manifest distinguishes 418 documentation-only descriptions from one verifier change. Historical source/evidence is never rewritten to make it appear retrospectively documented.

Current English publication changes explanatory text, notebook Markdown and declared presentation labels. Source/hash checks retain the actual as-run Git blobs; permitted documentation/presentation differences are checked narrowly. An English caption does not change an original screenshot or native output.

The five-class design document explains the implemented adapters, factory and dependency injection. Clone verification is a PC check, not proof that a physical laptop or a new VM has been provisioned.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
python -S scripts/verify-final-handover.py
```

```powershell
python -m venv outputs/venv-check
./outputs/venv-check/Scripts/python.exe -m pip install -r docs/evidence/repository-audit/frozen/freeze.stdout
./outputs/venv-check/Scripts/python.exe -m pytest -q tests --hypothesis-seed=20261028
```

```powershell
python scripts/audit-repository.py --output outputs/my-repository-audit --edit-proof docs/evidence/repository-audit/docstring-edits.json
```

## Implementation and evidence

- [Recorded artifact: repository-audit/frozen/pytest.stdout](evidence/repository-audit/frozen/pytest.stdout)
- [Recorded artifact: repository-audit/frozen/junit.xml](evidence/repository-audit/frozen/junit.xml)
- [Recorded artifact: repository-audit/frozen/source-before.json](evidence/repository-audit/frozen/source-before.json)
- [Recorded artifact: repository-audit/frozen/source-after.json](evidence/repository-audit/frozen/source-after.json)
- [Recorded artifact: repository-audit/frozen/verification.json](evidence/repository-audit/frozen/verification.json)
- [Recorded artifact: repository-audit/docstring-edits.json](evidence/repository-audit/docstring-edits.json)
- [Recorded artifact: repository-audit/manifest.json](evidence/repository-audit/manifest.json)
- [Source: infra/observability/requirements.lock](../infra/observability/requirements.lock)
- [Recorded artifact: repository-audit/initial-collection/console.stdout](evidence/repository-audit/initial-collection/console.stdout)
- [Recorded artifact: repository-audit/frozen/freeze.stdout](evidence/repository-audit/frozen/freeze.stdout)
- [Source: README.md](../README.md#architecture)
- [Recorded artifact: final-release-pc/manifest.json](evidence/final-release-pc/manifest.json)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/a395c4ea47b8f8e235556ab93de3d699ed9df18b/docs/repository-quality.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
