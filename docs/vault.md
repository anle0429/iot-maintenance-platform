# Vault secrets, scoped consumers and restart recovery

The lab uses a non-development Vault server with TLS and retained Raft storage. Two real AppRole consumers have separate policies: Jenkins reads an Airflow credential at job execution, and a gateway-sync job reads versioned Basic-auth material.

Native checks cover login/read/revoke, denied cross-policy access, KV2 CAS conflicts and credential rotation. Original Jenkins build 2 succeeded; the later after-restart build 3 repeated the scoped consumer path. Old gateway credentials fail after rotation while the new version works on all four hosts.

Restart leaves Vault sealed until the existing unseal material is used; it is not initialized again. Cluster identity and secret versions persist. Operator/root material, role secret IDs and passwords remain in protected runtime files and are not archived in public proof.

This is a one-VM, one-operator lab with two consumers. It does not establish enterprise-wide migration, HA or auto-unseal. Token/secret-ID renewal and current certificate validity remain operational prerequisites.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
python -S scripts/verify-vault.py
python -m unittest tests.test_vault_client -v
```

## Implementation and evidence

- [Source: scripts/verify-vault.py](../scripts/verify-vault.py)
- [Source: infra/ci/VaultAccess.Jenkinsfile](../infra/ci/VaultAccess.Jenkinsfile)
- [Source: scripts/vault-ci-consumer.py](../scripts/vault-ci-consumer.py)
- [Source: scripts/sync-gateway-vault.py](../scripts/sync-gateway-vault.py)
- [Source: infra/security/vault_client.py](../infra/security/vault_client.py)
- [Recorded artifact: vault-linux/final-readback/policy-ci.json](evidence/vault-linux/final-readback/policy-ci.json)
- [Recorded artifact: vault-linux/final-readback/role-gateway.json](evidence/vault-linux/final-readback/role-gateway.json)
- [Recorded artifact: vault-linux/ci-2/verification.json](evidence/vault-linux/ci-2/verification.json)
- [Recorded artifact: vault-linux/airflow-token-contract.json](evidence/vault-linux/airflow-token-contract.json)
- [Recorded artifact: vault-linux/rotation/rotation.json](evidence/vault-linux/rotation/rotation.json)
- [Recorded artifact: vault-linux/sync-v2/verification.json](evidence/vault-linux/sync-v2/verification.json)
- [Recorded artifact: vault-linux/final-readback/gateway-four-routes.json](evidence/vault-linux/final-readback/gateway-four-routes.json)
- [Recorded artifact: vault-linux/restart/verification.json](evidence/vault-linux/restart/verification.json)
- [Recorded artifact: vault-linux/restart/scoped-audit.jsonl](evidence/vault-linux/restart/scoped-audit.jsonl)
- [Source: infra/security/vault-assets.json](../infra/security/vault-assets.json)
- [developer.hashicorp.com / raft](https://developer.hashicorp.com/vault/docs/configuration/storage/raft)
- [Recorded artifact: vault-linux/privacy-audit.json](evidence/vault-linux/privacy-audit.json)
- [Recorded artifact: vault-linux/screenshots/vault-active-health.jpg](evidence/vault-linux/screenshots/vault-active-health.jpg)
- [Recorded artifact: vault-linux/initial-failure/journal.txt](evidence/vault-linux/initial-failure/journal.txt)
- [Recorded artifact: vault-linux/initial-failure/repair.json](evidence/vault-linux/initial-failure/repair.json)
- [Recorded artifact: vault-linux/sync-v1-readback/verification.json](evidence/vault-linux/sync-v1-readback/verification.json)
- [Source: scripts/deploy-private-vault.py](../scripts/deploy-private-vault.py)
- [developer.hashicorp.com / approle](https://developer.hashicorp.com/vault/api-docs/auth/approle)
- [developer.hashicorp.com / kv-v2](https://developer.hashicorp.com/vault/api-docs/secret/kv/kv-v2)

## Recorded interface views

These are original captures from the runs described above. Their timestamps, model identities and visible states belong to those runs.

![Recorded jenkins vault success](evidence/vault-linux/screenshots/jenkins-vault-success.jpg)


## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/a395c4ea47b8f8e235556ab93de3d699ed9df18b/docs/vault.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
