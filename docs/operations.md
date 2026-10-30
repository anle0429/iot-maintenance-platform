# Run and operate the platform

The repository supports a small local generator and offline verification without a VM. Live ML/agent demonstrations use the retained private GCP lab. They require its datasets, images, service state, model artifacts and protected credentials; these are not distributed in Git.

## Offline verification

Use Python 3.12/3.13 and Git from the repository root:

```powershell
$tag = Get-Date -Format 'yyyyMMdd-HHmmss'
python -B -S scripts/audit-final-coursework.py --output "outputs/audit-$tag"
```

The audit restores the [original receipt bundle](evidence-guide.md), checks all 117 rubric mappings and runs 59 artifact verifiers. To run an individual verifier first, restore once with `python -B -S scripts/unpack-evidence.py`. Neither operation starts services or sends a model request.

For a fresh local data run, use the [generator entry point](../generator/README.md). Heavy data processing and native API tests have separate container/runtime requirements in their component reports.

## Retained lab recovery

Administration uses IAP and loopback viewers. Keep the existing VM identities, private networking and eight-hour auto-STOP policy. Recover the existing resources only within an authorized session; check their actual state before starting a stopped instance. These commands do not provision a new lab.

In PowerShell, collect actual Compute descriptors after the CPU/GPU instances are RUNNING:

```powershell
$ProjectId = 'YOUR_PROJECT_ID'
$tag = Get-Date -Format 'yyyyMMdd-HHmmss'
$gcloud = (Get-Command gcloud.cmd -ErrorAction Stop).Source
$vmRoot = '/srv/iot-data/workspace/iot-maintenance-platform'
New-Item -ItemType Directory -Force outputs | Out-Null
& $gcloud compute instances describe iot-cw-vm --project $ProjectId --zone us-central1-a --format=json |
  Set-Content -Encoding utf8 "outputs/cpu-$tag.json"
& $gcloud compute instances describe iot-cw-gpu --project $ProjectId --zone us-central1-a --format=json |
  Set-Content -Encoding utf8 "outputs/gpu-$tag.json"
& $gcloud compute scp "outputs/cpu-$tag.json" iot-cw-vm:/tmp/iot-cpu.json --project $ProjectId --zone us-central1-a --tunnel-through-iap
& $gcloud compute scp "outputs/gpu-$tag.json" iot-cw-vm:/tmp/iot-gpu.json --project $ProjectId --zone us-central1-a --tunnel-through-iap
& $gcloud compute scp scripts/resume-demo-services.py scripts/resume-gpu-services.py scripts/refresh-agent-features.py infra/security/vault-assets.json iot-cw-vm:/tmp/ --project $ProjectId --zone us-central1-a --tunnel-through-iap
```

On the CPU VM, from the project workspace:

```bash
CW_ROOT=/srv/iot-data/workspace/iot-maintenance-platform
CW_TAG=$(date -u +%Y%m%d-%H%M%S)
sudo python3 -B /tmp/resume-demo-services.py --runtime-root "$CW_ROOT" \
  --descriptor /tmp/iot-cpu.json --vault-assets /tmp/vault-assets.json \
  --renew-consumers --output "$CW_ROOT/outputs/services-$CW_TAG"
sudo python3 -B /tmp/resume-gpu-services.py --runtime-root "$CW_ROOT" \
  --descriptor /tmp/iot-gpu.json --output "$CW_ROOT/outputs/gpu-$CW_TAG"
```

The CPU helper preserves retained Vault/registry/container state and the existing scoped monitoring identities. The GPU helper records the descriptor's actual start time, waits for the retained Ready node and explicitly loads the two existing model aliases. Model status checks generate no answer. Never initialize retained Vault again or replace a genuine session start with the current clock.

If worker/member addresses changed, follow the scoped [Substrate recovery](agent-restart-recovery.md). Inspect actual KEDA conditions and current worker registrations. The legacy CPU model remains parked while the GPU route is selected.

## Feature data

MCP readiness and positive predictions depend on a genuine one-hour wall-clock feature lease. Reuse an unexpired prepared state. After the old physical keys expire, create a new run:

```bash
sudo python3 -B /tmp/refresh-agent-features.py --runtime-root "$CW_ROOT" \
  --runtime-source "$CW_ROOT/data/observability-source-2fceb32/tree" \
  --run "demo-$CW_TAG" --output "$CW_ROOT/outputs/features-$CW_TAG"
```

The helper refuses a live cache, existing run state, insufficient resources or an inadequate remaining session. Native Feast SDK materializes 200 machines and deploys an immutable seed. If SDK preparation succeeded but Helm failed, inspect the retained failure; `--resume-deploy` can reuse that same still-valid prepared state with a fresh output directory. It does not reset timestamps. An expired state requires a new genuine preparation.

## ML demo

Keep a native port-forward running on the CPU VM:

```bash
sudo k3s kubectl port-forward --namespace=iot-features --address=127.0.0.1 service/iot-feature-api 18082:80
```

In a separate PC terminal, open the IAP tunnel:

```powershell
& $gcloud compute ssh iot-cw-vm --project $ProjectId --zone us-central1-a --tunnel-through-iap `
  --ssh-flag=-L --ssh-flag=127.0.0.1:18082:127.0.0.1:18082 --command="sleep 3300"
```

Then run `python -B -S scripts/demo-feature-api.py --base-url http://127.0.0.1:18082 --output "outputs/http-$tag"`. Its original HTTP receipts cover health, feature retrieval, independent model-6 arithmetic, replay and 409/404/422 rejection cases. This is an IAP administrative connection; separate [gateway tests](private-gateway.md) establish TLS/auth/rate controls.

## Agent demo

Require the llm-d/observer route, three production agents, current worker addresses, two ready endpoints for each MCP service and at least five minutes of genuine feature lease. The two experimental retrieval A/B agents share the existing pool and must be preserved.

On the CPU VM, run the retained sealed fourteen-input source:

```bash
CW_AGENT_SOURCE=$CW_ROOT/data/llm-source/learner-demo-f845b5b/tree
sudo python3 -B "$CW_AGENT_SOURCE/scripts/run-agent-demo.py" \
  --runtime-root "$CW_ROOT" --source "$CW_AGENT_SOURCE" \
  --deployment "$CW_ROOT/outputs/agent-gateway-29a" \
  --credentials "$CW_ROOT/data/runtime/agent-gateway-29a-private/credentials.json" \
  --output "$CW_ROOT/outputs/agents-$CW_TAG"
```

The command sends one coordinator question through the authenticated private HTTPS UI gateway and fetches both actual child histories. Check the fresh verification and original task responses. Successful acceptance requires two completed child answers, three retrieval tools and one drift tool, matching source/model identities and preserved parent values. Failed replies remain failures; there is no automatic question retry for a better answer.

Use the [private UI procedure](agent-gateway.md#run-the-private-demo-again) to inspect the same context. Expanding a delegation card is not proof of task success: the [post-restart validation](agent-runtime-validation.md) retains a misleading Completed wrapper around a failed child.

## Monitoring budget and diagnostics

The separate [steady-state overlay](../scripts/configure-llm-steady-state.py) preserves the observer image/code and health paths while applying the measured scrape/probe budgets. Ordinary restart retains it. After reapplying a base chart or monitoring setup, apply the overlay from its retained three-input source:

```bash
CW_STEADY_SOURCE=$CW_ROOT/data/llm-source/steady-cd42ddc/tree
sudo python3 -B "$CW_STEADY_SOURCE/scripts/configure-llm-steady-state.py" \
  --runtime-root "$CW_ROOT" --output "$CW_ROOT/outputs/steady-$CW_TAG"
```

It checks the expected deployment baseline, UID/resource version, Prometheus configuration and file permissions, then observes thirteen health samples. It does not bypass failed backend checks, delete telemetry or change authentication. The [recorded diagnosis](agent-runtime-validation.md) distinguishes the observed failure, configuration change and bounded stability result.

For metrics/logs/traces, reuse the existing private [monitoring viewers](llm-monitoring-demo.md). Inspect existing port owners before opening another tunnel. Keep credentials and descriptor/runtime files local. A completed screenshot or offline audit does not establish current readiness.
