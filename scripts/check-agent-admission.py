"""Run the real pinned OPA against independently declared allowed/bad production agent releases without deploying any candidate."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import argparse,json,subprocess,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agents.admission import canonical,evaluate,require_same_admitted_input
from agents.admission_cases import cases


def run(args):
    """Keep every actual input/decision/exit and prove that a post-admission tool edit cannot reuse an allow receipt."""
    seal=json.loads(args.source_manifest.read_bytes());assert seal['format']=='iot-agent-admission-source-v1'
    for name,pin in seal['files'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==pin,name
    out=args.output.resolve();out.mkdir(exist_ok=False);(out/'source.json').write_text(json.dumps(seal,indent=2)+'\n')
    checked=subprocess.run([str(args.opa),'check','--strict',str(ROOT/'infra/policy/agent-admission.rego')],capture_output=True,timeout=20)
    (out/'opa-check.stdout').write_bytes(checked.stdout);(out/'opa-check.stderr').write_bytes(checked.stderr);assert checked.returncode==0
    (out/'opa-version.txt').write_bytes(subprocess.check_output([str(args.opa),'version'],timeout=10))
    observed=json.loads((ROOT/'tests/fixtures/agent-admission-observed.json').read_bytes());declared=cases(observed);assert len({v['id'] for v in declared})==len(declared)
    rows=[]
    for row in declared:
        decision=evaluate(ROOT,args.opa,row['input'],out/row['id'])
        assert decision['allowed']==row['expected_allowed'],row['id']
        if decision['allowed']:require_same_admitted_input(decision,row['input'])
        else:
            try:require_same_admitted_input(decision,row['input'])
            except ValueError:pass
            else:raise AssertionError('A denied input authorized deployment')
        rows.append({'case':row['id'],'expected_allowed':row['expected_allowed'],**decision})
    baseline=rows[0];changed=json.loads(canonical(observed));agent=next(r for r in changed['resources'] if r['kind']=='SandboxAgent' and r['metadata']['name']=='iot-retrieval')
    agent['spec']['declarative']['tools'][0]['mcpServer']['toolNames'].append('start_pump')
    try:require_same_admitted_input(baseline,changed)
    except ValueError:pass
    else:raise AssertionError('Post-admission configuration tampering was admitted')
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':seal['commit'],'cases':rows,
        'allowed_cases':sum(r['allowed'] for r in rows),'denied_cases':sum(not r['allowed'] for r in rows),'post_admission_tool_edit_rejected':True,
        'deployment_commands_run':0,'model_requests':0,'scope':'Actual OPA evaluation of the complete eleven-resource production agent release and explicit known-answer counterexamples. CI integration and real deployment evidence are separate; no admission-webhook or universal security claim.'}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['opa','output','source-manifest']:p.add_argument('--'+name,type=Path,required=True)
    run(p.parse_args())
