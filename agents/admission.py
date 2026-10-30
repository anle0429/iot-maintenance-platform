"""Evaluate a complete production agent release with pinned OPA before any deployment command is allowed."""
from copy import deepcopy
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import json,subprocess

FORMAT='iot-agent-admission-v1'


def canonical(value):
    """Bind finite structured configuration independently of whitespace and Kubernetes status metadata."""
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def deployment_envelope(documents,model):
    """Include every rendered resource and the live shared model; keep entire specs and resolve only the fixed target namespace."""
    resources=[]
    for body in [*documents,model]:
        if body is None:continue
        if not isinstance(body,dict) or not isinstance(body.get('metadata'),dict):raise ValueError('Require actual structured resource metadata')
        metadata=body['metadata']
        resources.append({'apiVersion':body.get('apiVersion'),'kind':body.get('kind'),
            'metadata':{'name':metadata.get('name'),'namespace':metadata.get('namespace','iot-agents')},'spec':deepcopy(body.get('spec'))})
    return {'format':FORMAT,'namespace':'iot-agents','resources':resources}


def decision_value(payload):
    """Reject undefined, ambiguous or inconsistent OPA results instead of treating process success as policy acceptance."""
    try:
        rows=payload['result'];assert isinstance(rows,list) and len(rows)==1
        expressions=rows[0]['expressions'];assert isinstance(expressions,list) and len(expressions)==1
        result=expressions[0]['value'];assert set(result)=={'allowed','violations'} and type(result['allowed']) is bool
        violations=result['violations'];assert isinstance(violations,list) and all(isinstance(v,str) and v for v in violations)
        assert len(set(violations))==len(violations) and result['allowed']==(not violations)
    except (KeyError,IndexError,TypeError,AssertionError):raise ValueError('OPA did not return one explicit consistent admission decision') from None
    return result


def evaluate(source,opa,envelope,output):
    """Preserve original input, decisions and exit codes; fail on tool errors, changed policy or an unpinned binary."""
    source=Path(source);opa=Path(opa);output=Path(output);output.mkdir(parents=True,exist_ok=False)
    assets=json.loads((source/'infra/policy/opa-assets.json').read_bytes())
    if opa.stat().st_size!=assets['bytes'] or sha256(opa.read_bytes()).hexdigest()!=assets['sha256']:raise ValueError('The native OPA binary differs from the official asset lock')
    names=['infra/policy/agent-admission.rego','infra/policy/agent-admission-approved.json','agents/response-policy.json']
    pins={name:sha256((source/name).read_bytes()).hexdigest() for name in names};input_bytes=canonical(envelope)
    (output/'input.json').write_bytes(input_bytes);arguments=['eval','--strict','--strict-builtin-errors','--input',str(output/'input.json')]
    for name in names:arguments+=['--data',str(source/name)]
    def call(label,query,fail=False):
        """Keep the actual OPA console and fail closed on a missing or broken executable."""
        argv=[str(opa),*arguments,'--format=json',*(['--fail'] if fail else []),query]
        result=subprocess.run(argv,capture_output=True,timeout=30)
        (output/(label+'.stdout')).write_bytes(result.stdout);(output/(label+'.stderr')).write_bytes(result.stderr)
        (output/(label+'-exit.json')).write_text(json.dumps({'exit_code':result.returncode})+'\n');return result
    observed=call('decision','data.iot.agent_admission.result')
    if observed.returncode!=0:raise RuntimeError('OPA evaluation failed; no deployment is admitted')
    # Unification has no result when allowed is false; a comparison can return a defined false value with exit zero.
    decision=decision_value(json.loads(observed.stdout));gate=call('gate','data.iot.agent_admission.result.allowed = true',True)
    if gate.returncode!=(0 if decision['allowed'] else 1):raise RuntimeError('OPA gate exit disagrees with its explicit decision')
    for name,pin in pins.items():
        if sha256((source/name).read_bytes()).hexdigest()!=pin:raise RuntimeError('Admission policy changed during evaluation')
    if sha256((output/'input.json').read_bytes()).digest()!=sha256(input_bytes).digest():raise RuntimeError('Admission input changed during evaluation')
    report={'allowed':decision['allowed'],'violations':decision['violations'],'gate_exit':gate.returncode,'checked_utc':datetime.now(timezone.utc).isoformat(),
        'input_sha256':sha256(input_bytes).hexdigest(),'policy_files':pins,'opa_sha256':assets['sha256'],'opa_version':assets['version']}
    (output/'verification.json').write_text(json.dumps(report,indent=2)+'\n');return report


def require_same_admitted_input(receipt,envelope):
    """Refuse a denied decision or a later configuration change before executing the deployment command."""
    if receipt.get('allowed') is not True or receipt.get('gate_exit')!=0 or receipt.get('violations')!=[] or receipt.get('input_sha256')!=sha256(canonical(envelope)).hexdigest():
        raise ValueError('This exact deployment input has not been admitted')
