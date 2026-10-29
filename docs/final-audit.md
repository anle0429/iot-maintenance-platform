# Final rubric audit: evidence, scope and remaining work

## Current conclusion

The selected scope is now final ML plus final LLM/agents. The supplied workbook has 57 weighted ML rows and 60 weighted LLM rows, with 100 source points per sheet. These are source weights, not self-awarded marks. The original source cells and workbook bytes are preserved separately from the pinned English views.

Recorded ML lab evidence covers the linked ML requirements within documented boundaries. CPU inference and llm-d routing have [separate native proof](llm-inference.md). [RAG ingestion/governance](rag-orchestration.md) and [three grounded responses](llm-grounding.md) are also verified. MCP and agent requirements remain incomplete, so the whole selected coursework must not be described as complete. Shared infrastructure or ML API tests are not relabeled as MCP/agent proof.

## Audit method

The offline audit pins original source snapshots and reviewed English translations, checks IDs/merged-cell references/weights and validates linked evidence. It now runs 30 read-only artifact verifiers, including inference, RAG core, grounding and orchestration, and checks nine additional native capsules with 685 payloads. The older manifest explicitly excludes one nonessential coverage-HTML ignore file; source, reports and measurements remain verified.

The original ML source-language tracker is archived. Translation changes the presentation columns, not the preserved source requirements. A lowered threshold, missing row or changed translation pin fails the checks. The JSON report distinguishes inference, grounding and orchestration verification from the still-incomplete full LLM runtime and `coursework_complete=false`.

## Issues corrected during the ML review

Historical host-verifier checks were made aware of documentation-only edits while retaining immutable Git bindings. A clean Windows clone exposed a CRLF-derived snapshot pin; the canonical LF Git blob is now pinned. Native recovery restores transient monitor/relay/collector/Jenkins/Vault state and scoped credentials. Real Feast preparation refreshes expired features rather than changing timestamps. TLS renewal issues real certificates with protected existing keys and preserves Vault identity. MLflow UI access uses a loopback relay without replacing registry state.

The after-restart package verifies native materialization of 200 machines, nine actual HTTP cases, Vault CI build 3 SUCCESS/cross-and-revoked 403, four CA/SNI HTTPS routes and six reachable login/docs pages. Page 200 does not replace authenticated dashboard or DAG proof.

## Interpretation limits

The 100 GB workload covers Bronze/Silver, while ML uses 3.132 million rows from a separate 60-day source. Two executor Pods share one physical VM. Gateway domains/CA are private. Vault has two lab consumers; the mesh test is isolated. Stream CI uses a small fixture and full retained-window checks run separately. Original images and logs remain in their capture language. Laptop execution, production HA, full 100 GB ML and a complete LLM/agent system are not established.

## English documentation review

The later LLM/RAG implementation check on this PC passed 770 tests and 333 subtests, with seven documented skips, in 85.57 seconds. All 30 offline artifact verifiers passed. The repository audit found descriptions for 1,824 definitions in 353 maintained Python files. The native integration packages retain 145 grounding and 223 orchestration payloads, including the rejected predecessors and original browser captures. These checks leave the full agent track open.

The presentation review checked 1,823 local links and five generated architecture views. All 26 historical artifact verifiers passed. The PC regression suite passed 752 tests and 314 subtests, with seven documented skips. The repository audit found descriptions for all 1,745 definitions in 323 maintained Python files.

Notebook changes are confined to Markdown prose: executable cells, outputs, execution counts and metadata are unchanged. The dashboard change translates six display titles without changing queries or configuration. Earlier source/evidence files remain untouched. These checks validate the documentation and audit-tool changes; they do not establish new native workloads or close the LLM requirements.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
$tag = Get-Date -Format 'yyyyMMdd-HHmmss'
python -S scripts/audit-final-coursework.py --output "outputs/audit-$tag"
```

## Implementation and evidence

- [Recorded artifact: rubric-final/source.json](evidence/rubric-final/source.json)
- [Recorded artifact: final-review-live/manifest.json](evidence/final-review-live/manifest.json)
- [Recorded artifact: final-review-pc/manifest.json](evidence/final-review-pc/manifest.json)
- [javadoc.jenkins.io / CredentialsStore.html](https://javadoc.jenkins.io/plugin/credentials/com/cloudbees/plugins/credentials/CredentialsStore.html)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](demo-walkthrough.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/final-audit.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
