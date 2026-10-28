"""Verify native OPA decisions and actual Jenkins acceptance/rejection from immutable archived outputs."""
import json
from evidence_integrity import ROOT,verified,source_bindings
BASE=ROOT/'docs/evidence/deployment-policy-linux'


def main():
    """Cross-check48 declared inputs, original evaluator bytes, source seals and real gate exit codes."""
    read=verified(BASE,565,550);proof=read('negative5/verification.json');assert proof['passed'] and proof['deployment_commands_run']==0
    for number,result in [(3,'SUCCESS'),(5,'FAILURE')]:
        folder='' if number==3 else 'negative5/';job=read(folder+f'build-{number}/job.json');assert job['result']==result and not job['building'];base=folder+f'build-{number}/artifacts/build-{number}/out/'
        seal=read(base+'source-before.json');assert source_bindings(seal)==19 and seal==read(base+'source-after.json') and seal['commit']==proof['source_commit']
        assert int((BASE/(base+'candidate-gate.exit')).read_text())==(0 if number==3 else 1)
    for number in [1,2]:assert read(f'build-{number}/job.json')['result']=='FAILURE'
    prefix='build-3/artifacts/build-3/out/contracts/';contracts=read(prefix+'verification.json');assert contracts['passed'] and contracts['allowed_cases']==2 and contracts['denied_cases']==46 and len(contracts['cases'])==48
    assert contracts['binary_sha256']==json.loads((ROOT/'infra/policy/opa-assets.json').read_bytes())['sha256']
    seen=set()
    for row in contracts['cases']:
        name=row['case'];assert name not in seen;seen.add(name);decision=read(prefix+name+'-eval.stdout')['result'][0]['expressions'][0]['value'];gate=read(prefix+name+'-gate.stdout')
        assert decision=={'allowed':row['expected_allowed'],'violations':row['violations']} and bool(row['violations'])==(not row['expected_allowed']) and row['gate_exit']==(0 if row['expected_allowed'] else 1)
        if row['expected_allowed']:assert gate['result'][0]['expressions'][0]['value'] is True
        else:assert gate=={}
    assert {r['case'] for r in contracts['cases'] if r['allowed']}=={'observed-terraform','observed-application-envelope'}
    for name in ['historical-missing-seccomp','missing-limits','additional-public-address','public-ssh','wrong-model-version','changed-model-bytes','missing-model-pin','feature-protocol-mismatch']:assert name in seen
    missing=read(prefix+'missing-limits-input.json');assert missing['manifest']['spec']['template']['spec']['containers'][0]['resources']=={}
    assert 'RuntimeDefault' not in json.dumps(read(prefix+'historical-missing-seccomp-input.json')['manifest']['spec']['template']['spec']['securityContext'])
    supplement=read('negative5/native-manifest.json');assert len(supplement['files'])==10
    for n,v in supplement['files'].items():assert read('manifest.json')['files']['negative5/'+n]==v
    console=(BASE/'negative5/build-5/console.txt').read_text();assert 'Stage "Candidate accepted for review" skipped due to earlier failure(s)' in console and 'Policy accepted this candidate.' not in console and 'script returned exit code 1' in console
    safe=(BASE/'build-3/console.txt').read_text();assert 'Policy accepted this candidate.' in safe and 'Finished: SUCCESS' in safe
    # Jenkins wfapi inherits FAILED for the skipped downstream stage; preserve it and use the original console to establish non-execution.
    stages=read('negative5/build-5/stages.json');assert next(s for s in stages['stages'] if s['name']=='Candidate decision gate')['status']=='FAILED'
    print('PASS565payload/550native+10supplement;48knowninputs/2allow46deny;19Gitbindingsperfinalbuild;Jenkins3SUCCESSgate0/5FAILUREgate1/downstreamskipped;history1/2failuresand4earliersource retained;nodeploymentcommands. Outside-class confirmation pending.')


if __name__=='__main__':main()
