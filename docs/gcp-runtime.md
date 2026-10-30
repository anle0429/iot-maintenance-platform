# Measured Google Cloud runtime

Terraform created 14 resources for the authorized 8-vCPU/64-GiBVM and retained disks. Post-apply planning reported no drift. Ansible roles prepare common packages, storage, Docker and the bounded pilot; reruns report no changes or failures.

On the same 1,117,608,156-byte source, the baseline Spark configuration used local[2], a 1-GiBheap and HDD scratch; the optimized run used local[6], a 20-GiBheap and SSD scratch. Recorded elapsed time changed from 223.942 to 100.892 seconds while both settings passed 13 checks and 84 unit tests with 3,456,000 unique readings.

That 2.22 xcomparison belongs to this intermediate workload. Later 100 GB processing, Kubernetes serving and feature training have separate evidence. The current deployment/recovery guide describes retained state and private access after STOP/START; old successful receipts do not establish current endpoint readiness.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
gcloud compute instances start iot-cw-vm --project=YOUR_PROJECT_ID --zone=us-central1-a
gcloud compute ssh iot-cw-vm --project=YOUR_PROJECT_ID --zone=us-central1-a --tunnel-through-iap
```

```bash
cd /srv/iot-data/workspace/iot-maintenance-platform
python3 scripts/run-cloud-ingest.py --run-name practice-fast --mode optimized --source data/source/chunk-ingest-26 --receipt outputs/cloud-source-receipt.json --skip-build
$HOME/.venvs/iot-control/bin/ansible-playbook -i localhost, infra/ansible/bootstrap.yml --extra-vars=@$HOME/pilot-vars.json
```

## Implementation and evidence

- [Recorded artifact: gcp-runtime/cloud-deployment.json](evidence/gcp-runtime/cloud-deployment.json)
- [Recorded artifact: gcp-runtime/terraform-apply-redacted.txt](evidence/gcp-runtime/terraform-apply-redacted.txt)
- [Recorded artifact: gcp-runtime/verification.json](evidence/gcp-runtime/verification.json)
- [Recorded artifact: gcp-runtime/baseline/ingest-report.json](evidence/gcp-runtime/baseline/ingest-report.json)
- [Recorded artifact: gcp-runtime/optimized/ingest-report.json](evidence/gcp-runtime/optimized/ingest-report.json)
- [Recorded artifact: gcp-runtime/baseline/spark-metrics.json](evidence/gcp-runtime/baseline/spark-metrics.json)
- [Recorded artifact: gcp-runtime/optimized/spark-metrics.json](evidence/gcp-runtime/optimized/spark-metrics.json)
- [Recorded artifact: gcp-runtime/baseline/cloud-context.json](evidence/gcp-runtime/baseline/cloud-context.json)
- [Recorded artifact: gcp-runtime/optimized/cloud-context.json](evidence/gcp-runtime/optimized/cloud-context.json)
- [Source: infra/ansible/bootstrap.yml](../infra/ansible/bootstrap.yml)
- [Recorded artifact: gcp-runtime/ansible-idempotent.txt](evidence/gcp-runtime/ansible-idempotent.txt)
- [Recorded artifact: gcp-runtime/ansible-pilot.txt](evidence/gcp-runtime/ansible-pilot.txt)
- [Recorded artifact: gcp-runtime/host-report.json](evidence/gcp-runtime/host-report.json)
- [Source: scripts/run-cloud-ingest.py](../scripts/run-cloud-ingest.py)
- [Source: infra/ingest.cloud.compose.yaml](../infra/ingest.cloud.compose.yaml)
- [Recorded artifact: gcp-options/accelerated.json](evidence/gcp-options/accelerated.json)
- [Recorded artifact: gcp-runtime/storage-budget-estimate.json](evidence/gcp-runtime/storage-budget-estimate.json)
- [Recorded artifact: scale-source/vm-restart/sessions.json](evidence/scale-source/vm-restart/sessions.json)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/gcp-runtime.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
