# GPU inference and verified agent delegation

The private V100 worker now serves the three IoT agents through the shared llm-d endpoint. The recorded final run completed retrieval, drift and coordinator requests. Each specialist answer was checked against its own MCP results; the coordinator was checked against the actual child sessions it created.

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="images/platform-llm-plan.png">
  <img alt="Recorded agent and GPU architecture" src="images/platform-llm-plan.gif">
</picture>

The diagram describes the implementation. Original runtime payloads and process receipts are stored in the [evidence capsule](evidence/gpu-agents/manifest.json).

## What changed

The existing 8-vCPU/64-GiB VM remains the control and data node. A private 4-vCPU/15-GiB worker supplies one V100 with 16 GiB VRAM. An [Ansible role](../infra/ansible/roles/gpu_node/tasks/main.yml) joins the pinned K3s version, reserves the node with a label and taint, and configures the NVIDIA runtime. A limited eight-hour bootstrap credential is transported privately and stored outside Git.

The [GPU chart](../infra/charts/iot-gpu-model/templates/model.yaml) requests exactly one GPU and runs the same pinned Qwen2.5 7B GGUF weights used in the [offload experiment](gpu-inference.md). The device plugin and CUDA image are pinned by digest. Candidate checks compare the actual mounted weight hashes and GPU identity before promoting the router. A deliberately missing image exercised Helm's automatic rollback, followed by successful direct and routed generation checks.

One physical GPU requires a `Recreate` model rollout. The retained CPU backend provides a recovery option; automatic inference failover and uninterrupted GPU updates are not claimed.

## Why faster inference needed tool control

The initial GPU conversation still failed: repeated tool calls expanded its input beyond the 8,192-token context. Faster token generation did not correct the control flow.

The small [model-control adapter](../tools/model-control/main.go) now ends a Qwen generation at a complete tool-call closing marker. It forwards the backend's response bytes and status. Retrieval receives a three-call budget; drift receives one. SDK orchestration capabilities do not increase those specialist budgets. Once the budget is reached, the next model request synthesizes from the actual recorded results. Histories exceeding the absolute eight-call limit are rejected.

The adapter supplies generation controls. MCP performs the actual data access and prediction. The adapter does not execute tools or construct replacement answers. Policy `0.2.2` also requests one relevant vibration passage and all document IDs returned by that retrieval.

## Recorded results

Final native run: `agent-chat-29m`, 29 October 2026. These are single-run elapsed times, including the A2A request and actual tool work; they are not a latency SLA or a repeated benchmark.

| Role | Elapsed time | Verified behavior |
|---|---:|---|
| Retrieval | 4.39 s | Three successful MCP calls; P-001 timestamp, model version, risk and citations match the returned data. |
| Drift | 6.27 s | One MCP call; 32 unique machines, flagged features and reference/assessment IDs match. |
| Coordinator | 18.23 s | Two genuine delegations; both child sessions have independent MCP history and their complete answers are preserved. |

The [delegation checker](../agents/delegation_acceptance.py) rejects borrowed sessions, unpaired calls, changed child requests and altered answers. It checks the child tasks obtained from the controller API, rather than reusing the separately tested specialist runs.

The model remains version 6 of the simulated, uncalibrated pump-risk model. The feature snapshot was genuinely prepared for 200 machines with a one-hour lease. All final requests completed within that lease. The simulated event timestamp in November is distinct from the real October execution time.

The adapter's native Go tests cover 97.8% of statements. Boundary cases include request size, token budgets, cumulative history and HTTP methods; 100 seeded Unicode cases check idempotent normalization. The strict mutation audit records **116/140 mutants killed by actual failing tests (82.86%)**. Its denominator retains 16 survivors and eight compilation/harness failures. Nine duplicate mutations are reported separately. Original per-mutant test streams are retained. The earlier engine result counted the eight non-test failures as kills; that result is preserved as historical evidence. The offline checker recomputes the stricter score.

Version `0.2.2` of all three agents was published to the private catalog with the observed resource UID, specification hash and runtime image. The catalog records identities; it is not the deployment controller.

## Check the evidence locally

From the repository root, without starting the VM:

```powershell
python -S scripts/verify-gpu-agents.py
python -m pytest tests/test_agent_delegation_acceptance.py tests/test_gpu_backend_guards.py -q
```

With Go installed:

```powershell
cd tools/model-control
go test -v -cover ./...
```

For a live demonstration, follow the [agent demo](operations.md). Check both VM sessions and genuinely refresh the feature lease first. A repository clone contains source and captured evidence; model weights, running services, secrets and prepared feature data are local runtime state.

## Later milestones

This GPU checkpoint established the conversations described above. Subsequent reports supply their own original captures for [autoscaling](agent-scaling.md), [warm-up](agent-warmup.md), the [executed notebook](agent-notebook.md), [CI](ci-agents.md), [authenticated gateway](agent-gateway.md), [monitoring](llm-monitoring-demo.md), [agent A/B](agent-configuration-ab.md) and [model A/B](llm-model-ab.md). The [requirement map](final-audit-matrix.md) identifies the evidence for each rubric row. Earlier failed conversations and their timings remain part of this report.

The original gVisor diagnostic reached both MCP services and failed its egress check. A later [worker-policy experiment](agent-network-isolation.md) closes the direct specialist MCP boundary with actual allow/deny/allow-control receipts and post-policy conversations. The original failure remains unchanged. PID 1 is UID 0 inside gVisor; non-root execution and production multitenant isolation are not established.

References: [K3s bootstrap tokens](https://docs.k3s.io/cli/token), [K3s NVIDIA runtime support](https://docs.k3s.io/advanced#nvidia-container-runtime-support), [NVIDIA device plugin](https://github.com/NVIDIA/k8s-device-plugin/tree/v0.20.1), [llama.cpp function calling](https://github.com/ggml-org/llama.cpp/blob/7ac5727d6037ec99c31a28c6520f45de154199d9/docs/function-calling.md).
