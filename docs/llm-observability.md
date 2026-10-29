# Measuring model and agent activity

The observation API now has a source-tested private canary on the CPU VM, forwarding real requests to the existing V100 model server. Nine model requests produced exactly nine durable facts: three completed generations, four policy rejections and two invalid requests. The active agent route remains a separate integration step.

Run `python -B -S scripts/verify-llm-observer.py` to check the [quality report](evidence/llm-observer/quality-verification.json), [ready canary](evidence/llm-observer/canary-verification.json), [request results](evidence/llm-observer/probe-verification.json) and [original native archive](evidence/llm-observer/native-receipts.tar.gz). The checker requires only Python's standard library. It authenticates 894 original payloads, then independently checks mutation exits, reported usage, chunk clocks, request identities and the log/counter readbacks. This is recorded execution evidence, not a claim that a stopped VM is currently serving traffic.

## Source-tested canary

| Check | Recorded result |
|---|---|
| Native API tests | 93 cases and 305 subtests; no failures, errors or skips |
| Statement coverage | 492/492, or 100% |
| Branch coverage | 253/256, or 98.83% |
| Fresh mutation gate | 2,348 killed / 2,932 total, or 80.08%; 584 survivors retained |
| Mutation completion | Every mutant exited normally; no timeout, omission or engine-error kill |
| Running image | All six packaged runtime files match the tested source; authenticated OCI digest |
| Actual model requests | Three generations, four policy rejections and two invalid requests |
| Recorded usage | 125 input + 120 output = 245 total tokens across three generations |

The mutation inventory includes the actual request admission, streaming, finalization, trace receiver and middleware methods. An earlier factory-only inventory could not demonstrate that these handlers were tested. The strict gate accepts only normal failing-test exits as kills; an internal test-engine error cannot increase the score.

Two streamed canary replies had client TTFTs of 0.05090 s and 0.03485 s, with full times of 0.43238 s and 0.39729 s. The third reply arrived as one JSON document in 0.26934 s, so its TTFT is absent. These are functional canary measurements on different questions, not a performance comparison. The [per-request measurements](evidence/llm-observer/generations.json) preserve the exact values.

The original failed attempts are retained. The first quality build found incompatible dependency pins; completed mutation runs B, D and E stayed below the threshold, and C was deliberately interrupted to address uncovered handlers. The accepted F run starts with fresh scratch and keeps all survivors. Canary A stopped when the native image store lacked a digest reference. B stopped before workload creation because Helm had no explicit Kubernetes connection. C authenticated the immutable image reference, retained B's unchanged Secret and unused ledger, and completed installation with an explicit kubeconfig. These repairs did not alter the accepted API image or the existing agent/model specifications.

The canary's prompt checks cover four explicit patterns: email addresses, labeled telephone numbers, credential assignments and direct hardware-control commands. They do not establish universal PII detection or model safety. Logs retain approved identifiers, timings, statuses and counts; the observation ledger has no prompt, answer or credential fields. The fixed canary questions and original responses are retained separately as controlled test evidence.

## Earlier router pilot

The first measurement checkpoint records three real streaming requests through the private model router. Their token counts come from the provider's `usage` response. Their timings come from the client reading the stream. The [original measurements](evidence/llm-observation/measurements.json), [native execution report](evidence/llm-observation/pilot-verification.json) and [unaltered native archive](evidence/llm-observation/native-receipts.tar.gz) are retained together.

The established [GPU route](gpu-agent-routing.md) sends model-control requests to the V100 service. This pilot does not measure GPU utilization or compare inference engines. It checks that the stream can be observed correctly before adding monitoring to agent traffic.

## Read the measurements

| Request | Input tokens | Output tokens | Total tokens | Time to first output | Full response time |
|---|---:|---:|---:|---:|---:|
| Duplicate sensor events | 46 | 31 | 77 | 18.679 s | 18.935 s |
| Interpreting high vibration | 45 | 29 | 74 | 0.032 s | 0.260 s |
| Model and feature timestamps | 46 | 56 | 102 | 0.028 s | 0.473 s |

The slow first request is part of the result. These are different questions with different cache histories, so the table is not a controlled latency comparison. The pilot does not establish why the first request waited longer.

**Time to first output (TTFT)** starts when the client begins its request and ends when it receives actual generated text or a tool name/argument. An HTTP header, heartbeat, role announcement or empty tool fragment does not count. The full response timer continues until the stream ends. A response returned as one JSON document has no observable first-token timing and must report that value as absent.

The archive preserves the original clock measurements, requests and response bytes. Offline checks can independently recompute the reported token counts from those bytes. They cannot recreate elapsed wall time from an SSE transcript alone.

## Keep counts correct across replay

Each observed request has an identity. The ledger accepts its fact once, ignores an identical replay and refuses a conflicting replay. The native pilot saved three rows, replayed them, closed the database and reopened it. The count remained three. The offline verifier repeats that check against a copy of the original SQLite database.

Telemetry facts contain approved counts, timings and identifiers. They exclude prompts, answers and credentials. The pilot's three fixed demonstration prompts and raw responses are retained separately as explicit test evidence; production request content is not a ledger field.

## Verify this checkpoint yourself

From the repository root, run:

```powershell
python -B -S scripts/verify-llm-observation.py
```

This requires only Python's standard library and starts no cloud service. `passed: true` means the recorded archive, original source, reported usage, request identities and replay results agree. It does not mean a VM or dashboard is currently ready.

For the API exercises, create a small separate environment from the repository root. The dependency profile contains the observation API and its test tools:

```powershell
python -m venv .venv-llm-observer
$cwObserverPython = '.\.venv-llm-observer\Scripts\python.exe'
& $cwObserverPython -m pip install -r infra/llm-observability/requirements-local.txt
```

Then run the focused checks:

```powershell
& $cwObserverPython -B -m pytest tests/test_llm_observation_contracts.py tests/test_llm_telemetry_ledger.py tests/test_llm_observation_metrics.py tests/test_llm_private_traces.py tests/test_llm_observer_properties.py tests/test_llm_observer_quality.py tests/api/test_llm_observer_api.py -q
```

These exercise fragmented Unicode streams, missing or conflicting usage, replay after reopening, policy admission, exact response forwarding, private trace projection and failing upstream connections. They also reject a mutation report that counts an internal test-engine error as a successful test kill.

The local API exercises use controlled backends and temporary databases. Native GPU requests are recorded in the separate pilot archive. The complete fresh mutation gate runs in the retained Linux test image.

## What is being integrated next

The new observation API sits in front of the retained model-control adapter. It reads streaming bytes while forwarding them, exposes metrics and projects native OTLP traces onto an approved metadata schema. Prompt checks cover four explicit pattern categories: email, labeled telephone numbers, credential assignments and direct hardware-control commands. They are bounded coursework controls, not a universal PII detector or safety classifier.

A separate Helm release now runs the accepted canary behind an internal ClusterIP service and a narrow network policy. It retains the old adapter deployment and does not change the active model pool. Agent tracing, traffic promotion, native load checks, Prometheus/Loki integration and original dashboard captures remain separate acceptance steps.

The prepared internal API has explicit machine-readable failures. These response bodies are checked locally alongside their HTTP statuses:

| Endpoint | Condition | HTTP status | Error code |
|---|---|---:|---|
| Model | Body exceeds one MiB | 413 | `model_request_too_large` |
| Model | Observation ledger is full | 503 | `observation_capacity_reached` |
| Traces | Unsupported media type | 415 | `require_otlp_protobuf` |
| Traces | Invalid protobuf envelope | 400 | `invalid_otlp_trace` |
| Traces | Body exceeds two MiB | 413 | `otlp_request_too_large` |
| Traces | Trace storage is unavailable | 503 | `trace_storage_unavailable` |

Agent counts need special care: the pinned Go ADK creates one root `invocation` for an executor run, alongside nested agent/model/tool spans. Counting every nested span as another agent call would inflate the result. An unset trace status also stays unset; it is not evidence of success. Actual MCP execution failures remain visible through the MCP services' own counters.

For a presentation, explain the three distinctions first: provider-reported tokens versus estimates; first generated output versus HTTP headers; unique executions versus repeated telemetry delivery. Full LLM observability rows L50–L55 remain open until the live integrations and required captures have been verified.

The API separates shared adapters (`ObserverRuntime`) from each request's state (`ModelExchange`) and ASGI response status (`HttpObservation`). The mutation admission gate requires actual mutants for the request admission, stream, finalization, trace receiver and middleware methods. A factory-only inventory cannot qualify as API mutation evidence. Explicit malformed credentials and malformed tool declarations also have regression cases; neither may fall through to an unrelated private-file read or crash the role classifier.
