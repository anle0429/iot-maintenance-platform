# Terraform and Ansible verification

The IaC package preserves a real Terraform 1.16.6 plan with 14 no-opresources and native Ansible recaps for the existing VM. The observed reruns completed with zero changed/failed tasks, with bound inputs and original report/UI captures.

Terraform covers the approved cloud VM, network and disks. Ansible has roles for common setup, storage, Docker, the pilot and K3s preparation. Additional platform services use their own bounded CLIs/charts; this is not a claim that every service is managed entirely by Ansible.

The runtime is Compute VM based, not a GKE deployment. The scratch disk is pd-balanced, not pd-ssd. Administrative ingress uses IAP SSH 22. The plan was inspected without a new apply; original redacted native reports are retained as actual evidence.

## Implementation and evidence

- [Recorded artifact: iac-final/prior/docs/evidence/gcp-runtime/terraform-apply-redacted.txt](evidence/iac-final/prior/docs/evidence/gcp-runtime/terraform-apply-redacted.txt)
- [Recorded artifact: iac-final/terraform-read-only-redacted.txt](evidence/iac-final/terraform-read-only-redacted.txt)
- [Recorded artifact: iac-final/prior/tests/fixtures/policy-terraform-observed.json](evidence/iac-final/prior/tests/fixtures/policy-terraform-observed.json)
- [Source: infra/ansible/bootstrap.yml](../infra/ansible/bootstrap.yml)
- [Recorded artifact: iac-final/prior/docs/evidence/gcp-runtime/ansible-idempotent.txt](evidence/iac-final/prior/docs/evidence/gcp-runtime/ansible-idempotent.txt)
- [Source: infra/ansible/roles/kubernetes/tasks/main.yml](../infra/ansible/roles/kubernetes/tasks/main.yml)
- [Source: infra/ansible/kubernetes.yml](../infra/ansible/kubernetes.yml)
- [Recorded artifact: iac-final/prior/docs/evidence/kserve/cluster/idempotent-console.txt](evidence/iac-final/prior/docs/evidence/kserve/cluster/idempotent-console.txt)
- [Recorded artifact: iac-final/verification.json](evidence/iac-final/verification.json)

## Recorded interface views

These are original captures from the runs described above. Their timestamps, model identities and visible states belong to those runs.

![Recorded terraform apply](evidence/iac-final/screenshots/terraform-apply.jpg)

![Recorded ansible idempotent](evidence/iac-final/screenshots/ansible-idempotent.jpg)


## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/a395c4ea47b8f8e235556ab93de3d699ed9df18b/docs/iac-verification.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
