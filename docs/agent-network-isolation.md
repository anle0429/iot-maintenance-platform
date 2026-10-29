# Agent network isolation

The retrieval and drift agents run inside separate gVisor actors. Their dedicated worker pools now also have Kubernetes egress policies: retrieval workers can reach the retrieval MCP service, drift workers can reach the drift MCP service, and coordinator workers delegate through the controller. All three roles can use the shared private model route and required platform services.

This was checked on the existing private two-node lab on 29 October 2026, with kagent 0.10.3, Agent Substrate 0.0.9 and K3s v1.35.9+k3s1. The model, prompts and MCP releases were kept at the versions used in the [GPU conversation checkpoint](gpu-agent-routing.md).

## Why a separate network policy is needed

The earlier actor diagnostic reached both MCP services. The declarative agent's `allowedDomains` configuration did not enforce that direct-access boundary in this runtime configuration. The retained failure remains part of the evidence.

Substrate 0.0.9 gives each running actor a dedicated worker and masquerades its outgoing traffic behind that worker's Pod IP. The new [Helm chart](../infra/charts/iot-agent-network/templates/network.yaml) therefore selects each role's worker pool and restricts its **egress**. Kubernetes policies are additive; adding another restrictive-looking MCP ingress policy would not override the existing namespace-wide ingress permission.

The policies preserve Substrate's private control, DNS, snapshot and Valkey services, the observed private Kubernetes API address, the shared model router and the kagent controller. They do not create Internet access or a new cloud resource. The coordinator receives no direct MCP exception.

## What was measured

An operator resumed two already completed, source-correlated actors through the native TLS-verified Substrate API. A fixed static diagnostic ran inside each actor. It checked both owned MCP health endpoints and the absence of host coursework data and a Kubernetes service-account token. Actor UID, worker UID and Pod IP were recorded.

Each actor went through the same reversible experiment:

| Phase | Own MCP | Other MCP | Additional rejected packets |
|---|---:|---|---:|
| Policy active | HTTP 200 | Network rejection | 1 |
| Narrow temporary permission | HTTP 200 | HTTP 200 | 0 |
| Permission removed | HTTP 200 | Network rejection | 1 |

Both retrieval and drift passed all three phases. The counters came from the observed worker's actual Kube-router firewall chain. Both MCP services also returned HTTP 200 from the host. A failed request alone was never counted as proof of policy enforcement.

The temporary permissions were removed and both actors returned to their original suspended state. Fresh retrieval, drift and coordinator conversations then completed under the restrictive policies. Independent checks verified three retrieval MCP calls, one drift call and two real child-agent delegations against their own tool histories. The requests used a genuinely refreshed 200-machine feature snapshot within its one-hour lease.

## Recheck and inspect

From a clone, with no VM running:

```powershell
python -S scripts/verify-agent-network.py
python -m pytest tests/test_agent_network.py -q
```

The [offline checker](../scripts/verify-agent-network.py) authenticates 392 original native payloads, the applied source, process invocations, packet counters, lifecycle cleanup and post-policy answers. The payloads are stored byte-for-byte in a compressed [receipt archive](evidence/agent-network/native-receipts.zip), alongside its [inventory](evidence/agent-network/manifest.json). Original failed attempts are included. The older Go probe expected HTTP 403 and still reports its original negative result; the enclosing packet-counter experiment verifies the CNI rejection without rewriting that result.

On the VM, inspect the installed policies and pools:

```bash
sudo k3s kubectl get networkpolicy -n iot-agents -l iot.coursework/agent-network=true
sudo k3s kubectl describe networkpolicy iot-retrieval-worker-egress -n iot-agents
sudo k3s kubectl get workerpools -n iot-agents
```

For normal conversations, follow the [agent demo](agent-demo.md). The [guarded installer](../scripts/configure-agent-network.py) requires sealed project source, an idle actor inventory, the observed private control address and a fresh Helm release. Preserve the installed release when inspecting the lab; do not reinstall it to repeat a demo.

## Scope and presentation

Explain this as two layers: gVisor isolates the actor's filesystem and execution environment; the worker egress policy limits its direct service access. The measured PID 1 user is **UID 0 inside gVisor**. This result does not establish non-root execution, resistance to a compromised trusted platform, a complete domain allowlist or production multitenant isolation. Required native UI screenshots, autoscaling and warm-up benchmarks remain separate deliverables.

Source references: [kagent's SRT materialization conditions](https://github.com/kagent-dev/kagent/blob/v0.10.3/go/core/internal/controller/translator/agent_manifest_builder.go), [Substrate worker networking](https://github.com/kagent-dev/substrate/blob/v0.0.9/cmd/ateom-gvisor/main.go), [Kubernetes NetworkPolicy semantics](https://kubernetes.io/docs/concepts/services-networking/network-policies/).
