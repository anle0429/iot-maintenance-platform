# Google Cloud VM design and cost controls

The lab's selected machine has 8 vCPU and 64 GiBRAM. Terraform separates networking, the VM and retained storage; the deployed disks are 50 GiBboot,500 GiBstandard data HDD and 150 GiBpd-balanced scratch.

Planning checked actual project quota, region/zone availability, disk budgets and an authorized session before allocation. The VM uses IAP administration and an eight-hour maximum runtime with STOP rather than deletion. Retained disks continue to consume resources while stopped.

Historical cost estimates belong to their quoted configuration/date; they are not current pricing guarantees. The chosen VM was a user-approved coursework resource. New resources or sessions outside that authorization require an explicit decision, and secrets/state remain outside Git.

The runtime report contains actual apply/idempotency/benchmark evidence. A Terraform plan is preparation; it does not itself prove a service was deployed.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
./scripts/prepare-gcp.ps1 -Action Validate -RunName cloud-local-practice
./scripts/prepare-gcp.ps1 -Action Inspect -ProjectId YOUR_PROJECT_ID -RunName cloud-inspect-practice
./scripts/prepare-gcp.ps1 -Action Plan -ProjectId YOUR_PROJECT_ID -RunName cloud-plan-practice
```

## Implementation and evidence

- [Source: infra/gcp/capacity-profiles.json](../infra/gcp/capacity-profiles.json)
- [Recorded artifact: gcp-options/verification.json](evidence/gcp-options/verification.json)
- [Recorded artifact: gcp-options/terraform-test.txt](evidence/gcp-options/terraform-test.txt)
- [Recorded artifact: gcp-options/quota-checks.json](evidence/gcp-options/quota-checks.json)
- [Recorded artifact: gcp-options/accelerated.json](evidence/gcp-options/accelerated.json)
- [Source: infra/gcp/pricing-us-central1.json](../infra/gcp/pricing-us-central1.json)
- [Source: infra/gcp/estimate.py](../infra/gcp/estimate.py)
- [Source: infra/gcp/vm/main.tf](../infra/gcp/vm/main.tf)
- [Source: scripts/prepare-gcp.ps1](../scripts/prepare-gcp.ps1)
- [Recorded artifact: gcp-prepare/verification.json](evidence/gcp-prepare/verification.json)
- [Recorded artifact: gcp-prepare/terraform-test.txt](evidence/gcp-prepare/terraform-test.txt)
- [Recorded artifact: gcp-prepare/quota-checks.json](evidence/gcp-prepare/quota-checks.json)
- [Recorded artifact: gcp-prepare/cost-estimate.json](evidence/gcp-prepare/cost-estimate.json)
- [github.com / compute_instance.html.markdown](https://github.com/hashicorp/terraform-provider-google/blob/v8.8.1/website/docs/r/compute_instance.html.markdown)
- [developer.hashicorp.com / mocking](https://developer.hashicorp.com/terraform/language/tests/mocking)
- [docs.cloud.google.com / limit-vm-runtime](https://docs.cloud.google.com/compute/docs/instances/limit-vm-runtime)
- [docs.cloud.google.com / using-tcp-forwarding](https://docs.cloud.google.com/iap/docs/using-tcp-forwarding)
- [docs.cloud.google.com / set-up-oslogin](https://docs.cloud.google.com/compute/docs/oslogin/set-up-oslogin)
- [cloud.google.com / general-purpose](https://cloud.google.com/products/compute/pricing/general-purpose)
- [cloud.google.com / disks-image-pricing](https://cloud.google.com/compute/disks-image-pricing)
- [cloud.google.com / network-pricing](https://cloud.google.com/vpc/network-pricing#ipaddress)
- [docs.cloud.google.com / free-cloud-features](https://docs.cloud.google.com/free/docs/free-cloud-features)
- [docs.cloud.google.com / budgets](https://docs.cloud.google.com/billing/docs/how-to/budgets)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](demo-walkthrough.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/gcp-vm.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
