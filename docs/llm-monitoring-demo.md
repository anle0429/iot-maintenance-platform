# Demonstrate the model and agent monitoring

Start with one question: **what happened when the assistant answered?** Metrics show how much work ran, logs describe individual requests, and traces show the observed sequence of operations. These are three views of the same lab, with different counting rules.

## Review the recorded run

From the repository root:

```powershell
python -B -S scripts/verify-llm-monitoring.py
```

This check runs locally with Python's standard library. It authenticates the original CPU/GPU archives and recomputes the host-counter comparisons, 128 exact log events, 24 Grafana/Prometheus query matches and 108 load-request identities. It starts no service and does not spend cloud credit.

Read the [monitoring report](llm-observability.md#use-the-monitoring-views), then open the original Locust HTML:

```powershell
Start-Process ./docs/evidence/llm-monitoring/load/users-4.html
```

The final four-client report has **78 requests, zero failures and p95 2,300 ms**. The one-client report has **30 requests, zero failures and p95 970 ms**. Each question allows at most 16 generated tokens; these timings do not predict the latency of a long coordinator answer.

## Open the retained live viewers

Use the existing lab after its [recovery and session checks](operations.md). The VM needs its original runtime state, private credentials and model image; a Git clone contains none of those. Readiness and certificate expiry must be checked again for a later session.

The administrative viewer is [source-controlled](../scripts/view-llm-monitoring.py). It listens only on VM loopback and verifies the existing NGINX gateway's private CA, hostname and authentication. An IAP tunnel brings that viewer to PC loopback. The browser displays HTTP on loopback; it does not establish trust in the private lab certificate itself.

If these ports already have the retained viewers, reuse them:

| View | PC URL | What to inspect |
|---|---|---|
| Model and agents | [Runtime dashboard](http://127.0.0.1:18100/d/iot-llm-runtime) | HTTP outcomes, tokens, executor roots, MCP counters and policy hits |
| Policy frequency | [Controlled prompt window](http://127.0.0.1:18100/d/iot-llm-policy-window) | Nine unique rejections, overlapping reasons and exact versus rolling-minute frequency; [method and offline check](llm-policy-window.md) |
| GPU VM host | [Worker resources](http://127.0.0.1:18100/d/iot-llm-gpu-host) | Host CPU/RAM, root filesystem, disk and physical NIC rates |
| Request log | [Approved model events](http://127.0.0.1:18101/d/iot-llm-requests) | Per-request usage, complete response duration, streaming TTFT and policy categories |
| Agent trace | [Recorded coordinator graph](http://127.0.0.1:18102/trace/caee7d7a23dae19af7e76c39a18d8b26) | Coordinator, specialists, model calls and tool operations |
| Model artifacts | [Model comparison](http://127.0.0.1:18100/d/iot-llm-ab) | Four repeated contract cases on 1.5B and 7B; [recorded method](llm-model-ab.md) |
| Agent settings | [Agent comparison](http://127.0.0.1:18100/d/iot-agent-ab) | Three paired machine questions, original failures and verified tool-grounded answers; [recorded method](agent-configuration-ab.md) |

For a fresh viewer process, copy the maintained script to the approved VM:

```powershell
gcloud compute scp scripts/view-llm-monitoring.py iot-cw-vm:/tmp/iot-view-llm-monitoring.py --zone us-central1-a --tunnel-through-iap
```

On the VM, from its retained repository root, inspect the ports before starting another process. Resolve the actual private gateway address, then launch three viewers with fresh audit paths:

```bash
ss -ltn '( sport = :18100 or sport = :18101 or sport = :18102 )'
gateway_ip=$(sudo k3s kubectl get service iot-gateway -n iot-gateway -o jsonpath='{.spec.clusterIP}')
tag=$(date -u +%Y%m%dT%H%M%SZ)
for role in metrics logs traces; do
  sudo systemd-run --unit="iot-llm-view-$role-$tag" --property=RuntimeMaxSec=3600 --property=MemoryMax=256M \
    /usr/bin/python3 -B /tmp/iot-view-llm-monitoring.py --role "$role" --gateway-ip "$gateway_ip" \
    --credentials "$PWD/data/runtime/gateway-28-private/credentials.json" \
    --ca "$PWD/outputs/agent-gateway-29a/ca.crt" \
    --audit "$PWD/outputs/llm-view-$tag/$role.jsonl"
done
```

In a PC terminal, keep the IAP tunnel running while viewing:

```powershell
gcloud compute ssh iot-cw-vm --zone us-central1-a --tunnel-through-iap --command="sleep 3300" -- -L 127.0.0.1:18100:127.0.0.1:18100 -L 127.0.0.1:18101:127.0.0.1:18101 -L 127.0.0.1:18102:127.0.0.1:18102
```

This uses the current gcloud project and approved VM session. Close the tunnel after the demo; the viewers expire after one hour. Do not rerun the first-install discovery/dashboard operators against an existing installation: they deliberately refuse replacement of retained identities and configurations.

## Present it in three minutes

1. **Explain the workload.** One coordinator question invokes two specialists. That accepted cohort has three executor roots, four MCP executions and nine model generations. Spans are not another name for requests.
2. **Show an individual request.** In the request log, read input/output/total tokens and compare TTFT with full response duration. Usage comes from the provider. Nonstreaming requests have no observed TTFT.
3. **Show the machine and the test.** The host dashboard belongs to the GPU VM, while the Locust report measures actual model generation. Host RAM is different from GPU VRAM. A bounded 30-second test is not a maximum-throughput benchmark.

Keep the limits visible. The SDK leaves span statuses unset, and Jaeger displays `Incomplete` and `missing-service-name` warnings in the recorded graph. The proxy cannot reliably attribute every model request to an agent role. Historical MCP failures and the two old unavailable ML A/B targets remain visible. Policy checks cover four explicit patterns; they are not a general safety or PII guarantee.

The [rubric tracker](final-llm-rubric-tracker.md) keeps A/B comparisons, remaining delivery requirements and other unfinished work separate from this monitoring checkpoint.
