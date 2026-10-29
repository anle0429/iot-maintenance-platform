# CPU inference through llm-d

The first LLM milestone ran a real model behind the llm-d router on the existing coursework VM. It establishes inference and a measured configuration change for the **1.5B prototype**. The current backend is the [7B grounding candidate](llm-grounding.md); the prototype results below remain unchanged.

The later [V100 experiment](gpu-inference.md) compares zero versus GPU offload for the 7B model on a separate worker. Its source, image, hardware and measurements are recorded separately from the prototype below.

## What each part does

The **model file** contains learned weights. The **model server** loads those weights and generates tokens. The **router** accepts an OpenAI-compatible request and selects a model-server endpoint. A **tool call** is a structured request from the model to application code; generating that request does not execute the tool.

The current path is `client → llm-d/Envoy → llama.cpp → Qwen2.5`. Both Kubernetes Services are ClusterIP-only in the `iot-llm` namespace. No public ingress or paid external model API was added.

## Pinned configuration

| Item | Recorded configuration |
|---|---|
| Hardware | Existing single VM: 8 vCPU, 64 GiB RAM; AMD EPYC 7B12 with AVX2 |
| Model | Qwen/Qwen2.5-1.5B-Instruct-GGUF, revision `91cad51170dc346986eccefdc2dd33a9da36ead9` |
| Quantization | Q4_K_M; 1,117,320,736 actual bytes; Apache-2.0 model |
| Model server | llama.cpp image pinned by amd64 digest; native response fingerprint `b11960-8642541b2` |
| Router | llm-d standalone chart and endpoint picker v1.0.0; Envoy v1.33.2 |
| Inference CRD | Gateway API Inference Extension v1.6.2 |
| Context / slots | 4,096 context tokens; one generation slot |
| Backend limits | 4 CPU, 6 GiB RAM; two router containers have separate small limits |
| Scheduling | Router-observed in-flight token load; no claim of vLLM cache metrics or multi-backend scaling |

The default upstream CPU example requests much larger resources and a gated model. This lab keeps the required llm-d routing layer and supplies its own smaller OpenAI-compatible CPU backend. Weights stay on the existing SSD outside Git. The [model lock](../infra/llm/model.lock.json), [backend chart](../infra/charts/iot-llm/values.yaml) and [router values](../infra/llm/router-values.yaml) identify the actual configuration.

## Measured change

Only CPU generation/batch threads change from one to four. Each profile uses the same model digest, context, CPU limit, three fixed prompts, temperature zero, seed seven and 64-token cap. There are three warm-up requests followed by three repetitions of all three prompts. Actual responses report zero cached prompt tokens; generated texts are identical across both profiles.

| Measurement | 1 thread | 4 threads |
|---|---:|---:|
| Measured requests | 9 | 9 |
| Generated tokens across measured requests | 357 | 357 |
| Median first-token time | 1.745 s | 0.520 s |
| Median full-response time | 5.047 s | 1.682 s |
| Median completion tokens / full-response second | 7.727 | 23.185 |

The observed response-time ratio is 3.001 on this small workload. The client runs on the VM and calls the private Service. This is a sequential-client comparison, not a concurrency SLA, GPU comparison or model-quality evaluation. A separate three-request routed smoke test has a 1.760 s median response time; it is not a second nine-request optimization benchmark.

## Native failure and recovery

The initial run completed the baseline, upgraded the backend, then encountered `No route to host` when the Service data path had not settled. A later actual health call succeeded. Recovery retains the completed baseline, checks the same machine/model/configuration and waits for the Service health endpoint before measuring. It uses new source/output directories and preserves the failed initial report. Neither old measurements nor their timestamps were rewritten.

The resulting model response contains `tool_calls` for `get_machine_features` with `machine_id=P-001`. This verifies tool selection only. No MCP server was executed by that probe.

## Run and inspect

The [deployment CLI](../scripts/deploy-llm-inference.py) expects the retained VM, an approved unexpired session and a sealed source packet. It requires at least 12 GiB available RAM, 30 GiB free SSD and one hour remaining. It installs only into a fresh `iot-llm` namespace; an explicit recovery option preserves a completed baseline from a failed initial run. It does not provision a VM or extend its session.

To inspect the existing deployment on the VM:

```bash
sudo k3s kubectl get deployment,pod,service,inferencepool -n iot-llm
sudo k3s kubectl logs deployment/iot-model -n iot-llm --tail=30
sudo k3s kubectl logs deployment/iot-router-epp -n iot-llm --all-containers --tail=30
```

To expose the router only on the VM's loopback interface, keep this command open there:

```bash
sudo k3s kubectl port-forward -n iot-llm --address=127.0.0.1 service/iot-router-epp 18083:8081
```

From a separate PowerShell terminal on your PC/laptop, open IAP access:

```powershell
gcloud compute ssh iot-cw-vm --project YOUR_PROJECT_ID --zone us-central1-a --tunnel-through-iap --command="sleep 3600" -- -L 127.0.0.1:18083:127.0.0.1:18083
```

Then, in another PowerShell terminal:

```powershell
$body = @{
  model = 'iot-qwen2.5-7b'
  messages = @(@{role = 'user'; content = 'Explain why a high vibration reading alone does not prove a pump failure.'})
  max_tokens = 100
  temperature = 0
} | ConvertTo-Json -Depth 6
$reply = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:18083/v1/chat/completions -ContentType 'application/json' -Body $body
$reply.choices[0].message.content
$reply.usage
```

This command targets the current 7B backend and tests generation without retrieval. Use the [grounded probe](llm-grounding.md#repeat-the-live-probe-on-the-retained-vm) to retrieve and verify references. Neither command inspects a real machine.

Offline verification requires no running VM:

```powershell
python -S scripts/verify-llm-inference.py
```

The checker pins the [94-payload capsule](evidence/llm-inference/manifest.json), verifies uploaded source bytes, replays measurement arithmetic, checks actual readiness/image IDs and inspects the original structured tool response. It does not rerun inference or establish current endpoint readiness.

## Next dependency

The [LLM learning guide](llm-learning-guide.md) follows the full request. [RAG orchestration](rag-orchestration.md) and [grounded responses](llm-grounding.md) now have separate native proof. The [workstream](llm-agent-plan.md) tracks remaining global model configuration, MCP, specialist/coordinator agents, registry, sandbox, warm-up, CI, telemetry and A/B requirements.

Official references: [llm-d quickstart](https://llm-d.ai/docs/getting-started/quickstart), [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [Qwen model](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF).
