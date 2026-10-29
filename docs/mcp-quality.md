# MCP contract and mutation checks

**Status: 132 API tests pass and the complete local mutation gate passes at 681/850 (80.12%).** The timestamp fix is now in the current source; its native deployment remains open. The earlier VM mutation gates remain unsuccessful. Read the [native MCP report](mcp-tools.md) for the earlier deployed image, coverage, rollback and load evidence.

## What each check establishes

A baseline runs the original program and expects its tests to pass. Coverage records which statements and branches those tests execute. Mutation testing deliberately changes one condition or operation and runs the tests again. If the tests reject that change, the mutation is killed. A surviving mutation needs investigation: the behavior may be untested, or the change may be equivalent under the program's other constraints.

These checks answer different questions. Executing a line does not establish that a test would notice a wrong result. A mutation score also does not establish native deployment, performance, data freshness or agent correctness.

## A real contract failure

The native-backend adapter validates both machine features and prediction traces. Its operating-context timestamps must be ISO8601 strings. A controlled REST test supplied an upstream HTTP 200 response containing a null timestamp. It escaped validation as a Python attribute error, and the test client observed HTTP 500.

The isolated candidate checks the timestamp type before parsing it. Null, numeric, Boolean, list, object and missing values now follow the validation-error path. Both feature and prediction routes return a safe HTTP 503 response without forwarding the upstream diagnostic value. The original failing REST result is retained separately from the passing candidate results.

The new tests add 24 REST cases and 12 direct schema cases across the two timestamp fields and six malformed-value partitions. The candidate also adds 17 decision-boundary cases:

- A feature at exactly the prediction time is valid; a feature one microsecond in the future is rejected.
- A feature exactly 600 seconds old is valid; one beyond that limit is rejected even if its reported age was rounded.
- The one-millisecond age-field tolerance cannot override the actual timestamp bounds.
- A risk score equal to its model threshold produces the declared positive decision, including the valid zero/one edges.
- Two of eight shifted feature distributions reach the 0.25 drift alarm boundary; one does not. These cases use the actual histogram calculation.

The drift fixture moves selected values across a real reference-bin cut. Changing a constant reference value upward while remaining in the same upper bin would not establish histogram drift; a test must verify its data construction rather than assume every numerical change changes PSI.

## Why another mutation engine is being evaluated

The retained mutmut runs generated 945 mutations but none inside the decorated Pydantic validators in `contracts.py`. Their results remain 531/945 and 594/945 killed, both below the required >80%. They are not deleted or relabeled as successful.

The candidate uses Cosmic Ray 8.7.0 with the same four API modules: `auth.py`, `contracts.py`, `runtime.py` and `app.py`. It uses the default operators with no operator, line or module exclusions within that scope. The initialized inventory contains 850 mutations, including 305 in `contracts.py`. The engine and generated mutation families differ from mutmut, so their percentages must not be presented as directly comparable measurements of test improvement.

A deterministic first validator diagnostic physically widened the lower valid-age bound from 0 to -1. The earlier 115-test candidate did not notice it. After adding the temporal boundary tests, the same mutation failed on the one-microsecond-future case. The source file was restored and its hash checked after both diagnostics. This is a single diagnostic comparison, not a full mutation percentage.

The [Cosmic Ray tutorial](https://cosmic-ray.readthedocs.io/en/latest/tutorials/intro/index.html) describes initialization, baseline and execution as separate steps. The local run preserves those stages and starts a fresh session when the test command or tests change. The first Windows invocation resolved an unqualified `python` to an interpreter without pytest. Its baseline failed, so no mutation gate was executed or scored. The next session used the explicit test interpreter and passed its baseline.

## A strict score

The full run uses one local worker and a 60-second limit for each test process. The original 132-test suite passed before mutation. Every input source file has a recorded hash, and the worker is a separate copy under `outputs/`; the main checkout is not mutated.

The [scoreboard](../scripts/summarize-mcp-mutations.py) compares every job ID and mutation descriptor with the initial inventory. Omitting survivors, changing operators, dropping `contracts.py` or supplying an inventory reconstructed after the results existed is rejected. Timeouts, incompetent runs, skipped work and worker errors remain in the denominator and do not become kills. The required boundary is strictly greater than 80%, so four kills out of five do not pass.

```powershell
python scripts/summarize-mcp-mutations.py --inventory outputs/your-run/inventory.jsonl --results outputs/your-run/results.jsonl --output outputs/your-run/strict-score.json
```

Use the actual fresh run directory and preserve earlier reports. The scoreboard checks arithmetic and work identity. It does not authenticate a made-up input file or replace baseline, process-state and source-seal evidence. Its [tests](../tests/test_mcp_mutation_scoreboard.py) cover those counting boundaries separately.

## Recorded result

The run completed on the local Windows PC with a Ryzen 5 5500 and 16 GiB RAM. Its single worker took 3,165.22 seconds. The original CLI and result export both exited zero, and all 50 sealed source files matched the restored worker and promoted checkout. The unmutated baseline passed 132 tests; a separate checkout run after promotion passed the same 132 tests.

| Outcome | Count | Included as a kill? |
|---|---:|---|
| Tests rejected the mutation | 681 | Yes |
| Survived | 167 | No |
| Timed out | 1 | No |
| Worker error | 1 | No |
| Pending or skipped | 0 | No |
| **Total** | **850** | **681 / 850 = 80.1176%** |

The full initial inventory, all results, baseline, terminal receipt, exact source and earlier diagnostics are retained in the [84-payload evidence inventory](evidence/mcp-quality/manifest.json). The [offline verifier](../scripts/verify-mcp-quality.py) recomputes the score, compares exact job descriptors and checks the original baseline and source. It also checks mutations inside the decorated validators. The failed original null-timestamp case, first interpreter failure and surviving earlier validator diagnostic are retained.

```powershell
python scripts/verify-mcp-quality.py
```

For a new mutation environment, install the [API test dependencies](../infra/mcp/requirements-tests.txt) and [Cosmic Ray pin](../infra/mcp/requirements-cosmic-ray.txt) in a separate virtual environment. Follow the official initialize → baseline → execute sequence with all four API modules and an explicit test interpreter, using fresh output paths. The captured configuration records the original machine's paths and must not be overwritten to suit a different clone.

## Remaining acceptance

Build and deploy the source-bound timestamp fix and verify its native behavior. The existing deployment/load evidence describes the earlier source, with that difference reported explicitly by its checker. Agent conversation, isolation, scaling, warm-up, CI, telemetry and A/B requirements remain separate.
