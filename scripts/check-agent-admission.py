"""Run the real pinned OPA against independently declared allowed/bad production agent releases without deploying any candidate."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agents.admission import canonical,evaluate,require_same_admitted_input
from agents.admission_cases import cases
from agents.policy_execution import PolicyExecutionLedger


def run(args):
    """Keep every actual input/decision/exit and prove that a post-admission tool edit cannot reuse an allow receipt."""
    seal=json.loads(args.source_manifest.read_bytes());assert seal['format']=='iot-agent-admission-source-v1'
    required={'agents/admission.py','agents/admission_cases.py','agents/policy_execution.py',
              'scripts/check-agent-admission.py','infra/policy/agent-admission.rego',
              'infra/policy/agent-admission-approved.json','agents/response-policy.json'}
    if not required <= set(seal['files']):raise ValueError('The evaluator and all policy dependencies must be source-bound')
    for name,pin in seal['files'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==pin,name
    out=args.output.resolve();out.mkdir(exist_ok=False);(out/'source.json').write_text(json.dumps(seal,indent=2)+'\n')
    assets=json.loads((ROOT/'infra/policy/opa-assets.json').read_bytes())
    ledger=PolicyExecutionLedger(args.opa.resolve(),assets['sha256'])
    checked=ledger([str(args.opa.resolve()),'check','--strict',str(ROOT/'infra/policy/agent-admission.rego')],capture_output=True,timeout=20)
    (out/'opa-check.stdout').write_bytes(checked.stdout);(out/'opa-check.stderr').write_bytes(checked.stderr);assert checked.returncode==0
    version=ledger([str(args.opa.resolve()),'version'],capture_output=True,timeout=10)
    if version.returncode:raise ValueError('The pinned OPA executable did not report its version')
    (out/'opa-version.txt').write_bytes(version.stdout)
    (out/'opa-version.stderr').write_bytes(version.stderr)
    observed_path=getattr(args,'observed',None) or ROOT/'tests/fixtures/agent-admission-observed.json'
    observed=json.loads(observed_path.read_bytes());declared=cases(observed);assert len({v['id'] for v in declared})==len(declared)
    (out/'observed.json').write_bytes(canonical(observed))
    rows=[];binding_rejections=[]
    for row in declared:
        decision=evaluate(ROOT,args.opa,row['input'],out/row['id'],command_runner=ledger)
        assert decision['allowed']==row['expected_allowed'],row['id']
        if decision['allowed']:require_same_admitted_input(decision,row['input'])
        else:
            try:require_same_admitted_input(decision,row['input'])
            except ValueError:binding_rejections.append(row['id'])
            else:raise AssertionError('A denied input authorized deployment')
        rows.append({'case':row['id'],'expected_allowed':row['expected_allowed'],**decision})
    baseline=rows[0];changed=json.loads(canonical(observed));agent=next(r for r in changed['resources'] if r['kind']=='SandboxAgent' and r['metadata']['name']=='iot-retrieval')
    agent['spec']['declarative']['tools'][0]['mcpServer']['toolNames'].append('start_pump')
    try:require_same_admitted_input(baseline,changed)
    except ValueError:pass
    else:raise AssertionError('Post-admission configuration tampering was admitted')
    for name,pin in seal['files'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==pin,name
    processes=ledger.summary();(out/'process-ledger.json').write_text(json.dumps(processes,indent=2)+'\n')
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':seal['commit'],'cases':rows,
        'allowed_cases':sum(r['allowed'] for r in rows),'denied_cases':sum(not r['allowed'] for r in rows),'post_admission_tool_edit_rejected':True,
        'resource_count':len(observed['resources']),'observed_input_sha256':sha256(canonical(observed)).hexdigest(),
        'denied_receipt_bindings_rejected':binding_rejections,'process_ledger':processes,
        'scope':'Pinned OPA decisions, exit codes and denied configuration bindings for this exact input. The process ledger records OPA dispatches only; deployment/cluster/model side effects require independent CI observations.'}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    summary={k:v for k,v in report.items() if k not in {'cases','process_ledger','denied_receipt_bindings_rejected'}}
    summary['opa_command_count']=processes['invocation_count']
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['opa','output','source-manifest']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--observed',type=Path,help='Exact current rendered deployment envelope; defaults to the historical fixture')
    run(p.parse_args())
