# Source history migration

The repository history was filtered to remove nine personal learning and presentation documents. All 321 existing commits were retained, including their authors, timestamps and messages. Commit identities changed because Git includes each tree and parent identity in its commit hash.

The removed paths are `docs/agent-demo.md`, `docs/day1-generator.md`, `docs/day2-batch.md`, `docs/day3-streaming.md`, `docs/demo-walkthrough.md`, `docs/domain-proposal.md`, `docs/learner-demo.md`, `docs/llm-learning-guide.md` and `docs/presentation-guide.md`. They are absent throughout the filtered history. The runtime demo and verification scripts remain part of the submission.

## Original evidence and source identities

The [migration manifest](history-rewrite/manifest.json) maps the original main tip `fda38cd9ec2a6dee09ac00bd48caf6dbba855b09` to filtered tip `409cd58e717ebbe0e4c3b253e517b3dc523c7be9`, together with every preceding commit. Later maintenance commits build on that filtered tip.

Original native receipts still contain the commit identities captured when their workloads ran. Their bytes, source SHA-256 pins, measurements, unsuccessful outcomes and screenshots were not rewritten. The [historical source reader](../scripts/git_sources.py) authenticates the migration inventory, resolves an original identity and checks the exact retained Git blob. New source packages record their actual current commit identity.

The [metadata proof archive](history-rewrite/original-object-proofs.tar.gz) contains 321 original commit objects and 461 root/document tree objects. It contains no Git blob objects or removed document bodies. The [migration verifier](../scripts/verify-history-migration.py) reconstructs each filtered commit, checking that only the declared paths and parent identities changed. Retained file names, modes and blob identities, plus author/date/message bytes, must match. It also checks that the original changed commit objects and replacement refs are absent from the active Git database.

Maintained GitHub source links use the filtered identities. Two model-study input documents have only their declared navigation changes accepted by the checker; experimental content remains byte-bound. No native workflow or model request was repeated for this migration.

## Verify and use the filtered repository

```powershell
python -B -S scripts/verify-history-migration.py
python -B -S scripts/unpack-evidence.py
$tag = Get-Date -Format 'yyyyMMdd-HHmmss'
python -B -S scripts/audit-final-coursework.py --output "outputs/audit-$tag"
```

The full audit includes the migration check alongside the existing artifact checks. See [evidence storage](evidence-guide.md) for receipt restoration and [the final audit](final-audit.md) for the scope of the 117-requirement reconciliation.

Use a fresh clone after the history replacement. Preserve any local work separately before retiring an older checkout; merging or pushing an old branch can reintroduce the removed history. A private recovery backup is retained locally and is excluded from this repository.

Filtering the current repository does not guarantee removal from previously downloaded clones, forks or GitHub caches. [GitHub's history-rewrite guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository) explains these limits and the coordination required with other clones.
