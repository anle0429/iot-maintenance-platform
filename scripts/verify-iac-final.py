"""Cross-check historical Linux IaC receipts, fresh Windows read-only plan and public screenshot hashes."""
from pathlib import Path
from hashlib import sha256
import json,re
from evidence_integrity import ROOT,source_bindings
BASE=ROOT/'docs/evidence/iac-final'


def main():
    """Require exact payloads, retained source reports,14no-op resources and actual idempotent recaps."""
    manifest=json.loads((BASE/'manifest.json').read_bytes());actual={p.relative_to(BASE).as_posix() for p in BASE.rglob('*') if p.is_file() and p!=BASE/'manifest.json'};assert actual==set(manifest['files']) and len(actual)==13
    for n,v in manifest['files'].items():
        assert '..' not in Path(n).parts and not Path(n).is_absolute();raw=(BASE/n).read_bytes();assert len(raw)==v['bytes'] and sha256(raw).hexdigest()==v['sha256']
    seal=json.loads((BASE/'source.json').read_bytes());assert source_bindings(seal)==8
    for n,h in seal['files'].items():assert sha256((BASE/'prior'/n).read_bytes()).hexdigest()==h
    prior=BASE/'prior/docs/evidence';assert 'Apply complete! Resources: 14 added, 0 changed, 0 destroyed.' in (prior/'gcp-runtime/terraform-apply-redacted.txt').read_text()
    for path,ok in [(prior/'gcp-runtime/ansible-idempotent.txt',52),(prior/'kserve/cluster/idempotent-console.txt',20)]:assert re.search(r'localhost\s+: ok='+str(ok)+r'\s+changed=0\s+unreachable=0\s+failed=0',path.read_text())
    projection=json.loads((BASE/'prior/tests/fixtures/policy-terraform-observed.json').read_bytes());assert len(projection['resources'])==14 and all(r['actions']==['no-op'] for r in projection['resources'])
    proof=json.loads((BASE/'verification.json').read_bytes());assert proof['passed'] and proof['fresh_plan_exit']==0 and proof['fresh_plan_apply_run'] is False and proof['private_plan_sha256']==projection['observation']['raw_private_plan_sha256'] and 'No changes.' in (BASE/'terraform-read-only-redacted.txt').read_text()
    k3s=json.loads((prior/'kserve/cluster/verification.json').read_bytes());assert k3s['node']['ready'] and k3s['idempotent_changed_zero']
    print('PASS13payload/8Gitbindings;historicalTerraform14created/Ansible52and20okchanged0;freshTerraform14no-op/exit0/noapply;2originalfileviewerphotos. NoGKE/globalAnsibleclaim.')


if __name__=='__main__':main()
