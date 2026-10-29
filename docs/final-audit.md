# Final rubric audit: evidence, scope and remaining work

## Current conclusion

The selected scope is now final ML plus final LLM/agents. The supplied workbook has 57 weighted ML rows and 60 weighted LLM rows, with 100 source points per sheet. These are source weights, not self-awarded marks. The original source cells and workbook bytes are preserved separately from the pinned English views.

Recorded ML lab evidence covers the linked ML requirements within documented boundaries. CPU inference and llm-d routing have [separate native proof](llm-inference.md). [RAG ingestion/governance](rag-orchestration.md) and [three grounded responses](llm-grounding.md) are also verified. [Native MCP deployment and API/load checks](mcp-tools.md) have separate evidence, and a [complete local mutation gate](mcp-quality.md) now exceeds 80%. Full agent acceptance and remaining delivery requirements are incomplete, so the whole selected coursework must not be described as complete. Shared infrastructure or ML API tests are not relabeled as MCP/agent proof.

## Audit method

The offline audit pins original source snapshots and reviewed English translations, checks IDs/merged-cell references/weights and validates linked evidence. It now includes 43 read-only artifact verifiers, including GPU conversations, [direct MCP network isolation](agent-network-isolation.md) and [automatic worker scaling](agent-scaling.md), and checks nine additional native capsules with 685 payloads. The scaling package preserves 1,300 original native payloads and ten original UI captures. The [executed agent notebook](agent-notebook.md) adds 58 original native payloads and four unedited captures of its HTML export. The [matched actor-startup comparison](agent-warmup.md) retains 740 original payloads and its mixed performance results. [RAG delivery](ci-rag.md) and [three separate MCP/agent pipelines](ci-agents.md) now have native test/build/deploy/acceptance proof; the latter retains 1,605 original payloads, eleven builds and three unedited stage captures. The older manifest explicitly excludes one nonessential coverage-HTML ignore file; source, reports and measurements remain verified.

The original ML source-language tracker is archived. Translation changes the presentation columns, not the preserved source requirements. A lowered threshold, missing row or changed translation pin fails the checks. The JSON report distinguishes inference, grounding and orchestration verification from the still-incomplete full LLM runtime and `coursework_complete=false`.

## Issues corrected during the ML review

Historical host-verifier checks were made aware of documentation-only edits while retaining immutable Git bindings. A clean Windows clone exposed a CRLF-derived snapshot pin; the canonical LF Git blob is now pinned. Native recovery restores transient monitor/relay/collector/Jenkins/Vault state and scoped credentials. Real Feast preparation refreshes expired features rather than changing timestamps. TLS renewal issues real certificates with protected existing keys and preserves Vault identity. MLflow UI access uses a loopback relay without replacing registry state.

The after-restart package verifies native materialization of 200 machines, nine actual HTTP cases, Vault CI build 3 SUCCESS/cross-and-revoked 403, four CA/SNI HTTPS routes and six reachable login/docs pages. Page 200 does not replace authenticated dashboard or DAG proof.

## Interpretation limits

The 100 GB workload covers Bronze/Silver, while ML uses 3.132 million rows from a separate 60-day source. Two executor Pods share one physical VM. Gateway domains/CA are private. Vault has two lab consumers; the mesh test is isolated. Stream CI uses a small fixture and full retained-window checks run separately. Original images and logs remain in their capture language. Laptop execution, production HA, full 100 GB ML and a complete LLM/agent system are not established.

## English documentation review

The actor-startup review passed 869 tests and 542 subtests, with 11 skips, in 75.48 seconds. All 41 offline artifact verifiers passed. The native benchmark separately measured 24 fresh actors; mixed results remain visible rather than being counted as a general startup improvement.

The subsequent agent-notebook review passed 865 tests and 529 subtests, with 11 skips, in 79.17 seconds. All 40 offline artifact verifiers passed. The live notebook run separately completed seven code cells and four actual MCP calls; the offline test count does not substitute for that execution.

The worker-scaling review passed 860 tests and 523 subtests on the PC, with 11 skips, in 69.44 seconds. All 39 offline artifact verifiers passed. The documentation check covered 704 maintained text files, 2,129 local links and five architecture views. The following paragraphs retain earlier review results; they are not added to the current counts.

The previous LLM/RAG implementation check on this PC passed 770 tests and 333 subtests, with seven documented skips, in 85.57 seconds. All 30 offline artifact verifiers passed. The repository audit found descriptions for 1,824 definitions in 353 maintained Python files. The native integration packages retain 145 grounding and 223 orchestration payloads, including the rejected predecessors and original browser captures. These checks leave the full agent track open.

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

The MCP checkpoint adds 745 curated source/native/UI payloads, 79 native API tests, 97.74% statement coverage, 90.57% branch coverage and two authenticated Locust workloads. Its verifier also checks that the original 945-mutant gate failed at 56.19%; verifying that receipt does not make the mutation requirement pass. The expanded mutation gate also failed, at 62.86%, with the earlier 79-test/coverage baseline unchanged. Full agent integration remains open.

The current PC regression passed 773 tests and 333 subtests, with nine documented skips, in 87.08 seconds. The dedicated MCP environment separately passed 79 API tests and three evidence-oracle tests. All 31 offline verifiers passed. Documentation checks covered 597 maintained files, 1,949 local links and five diagrams; the current Python audit found descriptions for all 1,952 definitions in 368 maintained files.

## Agent platform checkpoint

The [native agent foundation](agent-platform.md) adds a shared local-model configuration, persistent private registry, three Ready gVisor templates and two workers per role. Its 528-payload verifier preserves six actual tool results across two incomplete retrieval attempts and explicitly leaves complete chat, boundary probes, autoscaling and warm-up unverified. The same model now exposes an 8,192-token context after a guarded one-node rollout. These checks do not close the selected LLM track.

The PC regression for this checkpoint passed 781 tests and 335 subtests, with seven documented skips, in 59.37 seconds. Fifteen focused client/evidence/rubric tests also passed. Documentation checks covered 629 maintained files, 2,005 local links and five figures. All 1,991 definitions in 383 maintained Python files have descriptions. The offline audit now includes 32 artifact verifiers; its agent-foundation result does not assert complete conversations or the full coursework.

## Agent response iteration

The [response baseline](agent-response-contract.md) adds 22 byte-preserved payloads from the terminal native chat run. Drift's final facts match its executed tool results for 32 unique machines, including three equal successful returns and one recovered argument error. Retrieval omitted the risk score, the coordinator has no response, and the whole process timed out. These outcomes remain visible; the successful drift answer does not establish the full conversation requirement.

Compact response policy 0.2, generation-aware rollout checks, immutable catalog-version checks and bounded transport-failure receipts are implemented and tested locally. They have not been deployed or accepted on the VM. The PC regression passed 807 tests and 397 subtests, with seven documented skips, in 67.69 seconds. A subsequent review added endpoint/context and catalog-image/version guards; the focused suite then passed 42 tests and 71 subtests. All 33 recorded-artifact verifiers passed. The historical 419-docstring-only edit proof is a separate claim and is not asserted for later implementation changes.

The baseline verifier authenticates the captured historical client through its original source seal. It separately reports that the current client has changed. Neither the new code nor a passing fixture is relabeled as deployed-agent proof. Full chat, boundary acceptance, autoscaling, warm-up, mutation, notebooks and the remaining LLM operational requirements are still open.

An independent clean clone of `dc43ae6` passed 808 tests and 403 subtests, with seven documented skips, in 68.69 seconds. All 33 artifact verifiers passed with a clean working tree; the report still sets full LLM runtime and coursework completion to false. Documentation checks covered 641 maintained files, 2,022 local links and five figures. The current Python description check covered 2,051 definitions in 392 files. These checks use the recorded lab artifacts and do not start a new VM session.
