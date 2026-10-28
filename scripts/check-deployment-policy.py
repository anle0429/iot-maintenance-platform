"""Run official pinned OPA as a pre-deployment decision gate on observed safe inputs and declared unsafe counterexamples."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime,timezone
from copy import deepcopy
import argparse,json,subprocess,re
ROOT=Path(__file__).resolve().parents[1]


def cases():
    """Keep real positive inventories and independent known-unsafe changes explicit; never apply a candidate."""
    tf=json.loads((ROOT/'tests/fixtures/policy-terraform-observed.json').read_bytes());app=json.loads((ROOT/'tests/fixtures/policy-kubernetes-observed.json').read_bytes());values=[('observed-terraform',tf,True),('observed-application-envelope',app,True)]
    def add(name,base,edit):
        """Create a separate counterfactual without mutating either retained positive input."""
        candidate=deepcopy(base);edit(candidate);values.append((name,candidate,False))
    vm=lambda v:next(r for r in v['resources'] if r['type']=='google_compute_instance')['values']
    disk=lambda v:next(r for r in v['resources'] if r['type']=='google_compute_disk')['values']
    firewall=lambda v:next(r for r in v['resources'] if r['type']=='google_compute_firewall')['values']
    pod=lambda v:v['manifest']['spec']['template']['spec'];container=lambda v:pod(v)['containers'][0]
    add('unknown-format',app,lambda v:v.update(format='unknown'));add('unknown-kind',app,lambda v:v.update(kind='unreviewed'))
    add('empty-inventory',tf,lambda v:v.update(resources=[]));add('extra-resource',tf,lambda v:v['resources'].append({'address':'unapproved.resource','type':'google_compute_instance','actions':['create'],'values':{}}))
    add('duplicate-address',tf,lambda v:v['resources'].append(deepcopy(v['resources'][0])))
    add('wrong-resource-type',tf,lambda v:v['resources'][0].update(type='unreviewed'))
    add('delete-disk',tf,lambda v:next(r for r in v['resources'] if r['type']=='google_compute_disk').update(actions=['delete']))
    add('larger-machine',tf,lambda v:vm(v).update(machine_type='e2-highmem-16'))
    add('extend-session',tf,lambda v:vm(v)['scheduling'].update(max_run_seconds=57600))
    add('delete-on-expiry',tf,lambda v:vm(v)['scheduling'].update(termination_action='DELETE'))
    add('public-ip',tf,lambda v:vm(v).update(access_configs=[{'nat_ip':'203.0.113.11'}]))
    add('oversized-boot',tf,lambda v:vm(v)['boot_disk'].update(size_gib=1000))
    add('extra-attached-disk',tf,lambda v:vm(v)['attached_disk_names'].append('unapproved-disk'))
    add('oversized-data-disk',tf,lambda v:disk(v).update(size_gib=1000))
    add('public-ssh',tf,lambda v:firewall(v).update(source_ranges=['0.0.0.0/0']))
    add('wrong-kubernetes-kind',app,lambda v:v['manifest'].update(kind='Pod'))
    add('system-namespace',app,lambda v:v['manifest']['metadata'].update(namespace='kube-system'))
    add('missing-pod-spec',app,lambda v:v['manifest'].update(spec={}))
    add('empty-containers',app,lambda v:pod(v).update(containers=[]))
    add('host-network',app,lambda v:pod(v).update(hostNetwork=True))
    add('automatic-api-token',app,lambda v:pod(v).update(automountServiceAccountToken=True))
    add('historical-missing-seccomp',app,lambda v:v.update(manifest=json.loads((ROOT/'docs/evidence/log-discovery-linux/check/first-deployment.json').read_bytes())))
    add('root-user',app,lambda v:pod(v)['securityContext'].update(runAsUser=0))
    add('allow-root',app,lambda v:pod(v)['securityContext'].update(runAsNonRoot=False))
    add('unreviewed-init',app,lambda v:pod(v).update(initContainers=[{'name':'unreviewed','image':'example.invalid/tool:latest'}]))
    add('host-filesystem',app,lambda v:pod(v).update(volumes=[{'name':'root','hostPath':{'path':'/'}}]))
    add('privilege-escalation',app,lambda v:container(v)['securityContext'].update(allowPrivilegeEscalation=True))
    add('retain-capabilities',app,lambda v:container(v)['securityContext'].update(capabilities={'drop':[]}))
    add('writable-root',app,lambda v:container(v)['securityContext'].update(readOnlyRootFilesystem=False))
    add('unbounded-cpu',app,lambda v:container(v)['resources']['limits'].update(cpu='8'))
    add('unbounded-memory',app,lambda v:container(v)['resources']['limits'].update(memory='64Gi'))
    add('missing-limits',app,lambda v:container(v).update(resources={}))
    add('wrong-model-version',app,lambda v:v['model_pin'].update(version='7'))
    add('candidate-production',app,lambda v:v['model_pin'].update(production=False))
    add('wrong-run-lineage',app,lambda v:v['model_pin'].update(run_id='0'*32))
    add('wrong-data-lineage',app,lambda v:v['model_pin'].update(data_version='0'*64))
    add('bundle-pin-mismatch',app,lambda v:v['model_pin'].update(portable_sha256='0'*64))
    add('changed-model-bytes',app,lambda v:v.update(artifact_sha256='0'*64))
    add('feature-protocol-mismatch',app,lambda v:v.update(context_protocol_sha256='0'*64))
    add('model-protocol-mismatch',app,lambda v:v['model_pin'].update(protocol_sha256='0'*64))
    add('missing-model-pin',app,lambda v:v.pop('model_pin'))
    assert len({n for n,_,_ in values})==len(values)
    return values


def run(opa,output,source_manifest=None):
    """Require native OPA decisions and actual gate exit0/1; retain every original input/decision/console."""
    output.mkdir(parents=True,exist_ok=False);assets=json.loads((ROOT/'infra/policy/opa-assets.json').read_bytes());assert sha256(opa.read_bytes()).hexdigest()==assets['sha256']
    seal=json.loads(source_manifest.read_bytes()) if source_manifest else None
    if seal:
        for n,h in seal['files'].items():assert '..' not in Path(n).parts and sha256((ROOT/n).read_bytes()).hexdigest()==h
        (output/'source-before.json').write_text(json.dumps(seal,indent=2)+'\n')
    def call(name,arguments):
        """Save the real compiler/evaluator bytes before any status assertion."""
        p=subprocess.run([str(opa),*arguments],capture_output=True);(output/(name+'.stdout')).write_bytes(p.stdout);(output/(name+'.stderr')).write_bytes(p.stderr);return p
    version=call('version',['version','--format=json']);assert version.returncode==0
    compile=call('strict-check',['check','--strict',str(ROOT/'infra/policy/deployment.rego')]);assert compile.returncode==0
    rows=[]
    for name,candidate,want in cases():
        assert re.fullmatch('[a-z0-9-]+',name);path=output/(name+'-input.json');path.write_text(json.dumps(candidate,indent=2)+'\n')
        common=['eval','--data',str(ROOT/'infra/policy/deployment.rego'),'--data',str(ROOT/'infra/policy/approved.json'),'--input',str(path)]
        evaluation=call(name+'-eval',[*common,'--format=json','data.iot.deployment.result']);assert evaluation.returncode==0
        decision=json.loads(evaluation.stdout)['result'][0]['expressions'][0]['value'];assert type(decision['allowed']) is bool and decision['allowed']==want and bool(decision['violations'])==(not want)
        gate=call(name+'-gate',[*common,'--fail','--format=json','data.iot.deployment.result.allowed == true']);assert gate.returncode==(0 if want else 1)
        rows.append({'case':name,'expected_allowed':want,'allowed':decision['allowed'],'violations':decision['violations'],'gate_exit':gate.returncode})
    if seal:
        for n,h in seal['files'].items():assert sha256((ROOT/n).read_bytes()).hexdigest()==h
        (output/'source-after.json').write_text(json.dumps(seal,indent=2)+'\n')
    proof={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'opa_version':assets['version'],'binary_sha256':assets['sha256'],'cases':rows,'allowed_cases':sum(r['allowed'] for r in rows),'denied_cases':sum(not r['allowed'] for r in rows),'deployment_commands_run':0,'scope':'Standalone pre-deployment policy gate on observed Terraform plan and authored probe/model envelope plus declared counterexamples. No admission webhook, global rollout or unknown-input security completeness claim.'};(output/'verification.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps({'passed':True,'allowed':proof['allowed_cases'],'denied':proof['denied_cases'],'deployment_commands_run':0}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--opa',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--source-manifest',type=Path);a=parser.parse_args();run(a.opa,a.output,a.source_manifest)
