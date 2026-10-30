"""Extend the retained agent pipeline with an OPA capability/budget decision before any production deployment command."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import argparse,importlib.util,json,os,subprocess,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agents.admission import deployment_envelope,evaluate,require_same_admitted_input
from agents.admission_ci import require_policy_phase
SPEC=importlib.util.spec_from_file_location('retained_agent_release',ROOT/'scripts/ci-agents.py')
BASE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(BASE)


class PolicyContext(BASE.Context):
    """Reuse the original test/build/rollback machinery while interposing one source-bound production admission stage."""
    driver='/src/scripts/ci-agents-policy.py'

    def __init__(self):
        """Keep the original three job identities and accept only a normal or explicitly synthetic denial scenario."""
        super().__init__();self.scenario=os.environ.get('IOT_AGENT_POLICY_SCENARIO','production')
        if self.scenario not in {'production','unsafe-tool'}:raise ValueError('Unknown controlled admission scenario')

    def source_check(self):
        """Require all transitive policy/runtime/test inputs in the original complete source seal."""
        super().source_check();name='infra/ci/agents-policy-source.json'
        if name not in self.seal['files']:raise ValueError('The additional policy source declaration is missing')
        declared=json.loads((ROOT/name).read_bytes())
        if declared.get('schema_version')!=1 or not declared['required'] or not set(declared['required'])<=set(self.seal['files']):
            raise ValueError('An admission dependency is absent from the complete CI source')

    def policy_receipt(self):
        """Refuse to launch the native deployment container without this production build's own allow decision."""
        value=json.loads((self.out/'policy.json').read_bytes())
        return require_policy_phase(value,self.identity,self.source_digest)

    def native(self,phase,cluster=False,docker=False):
        """Preserve existing control mounts and route deployment through the additional immediate configuration recheck."""
        if phase=='deploy-host':self.policy_receipt();phase='admitted-deploy-host'
        extra=['--env','JOB_BASE_NAME','--env','IOT_AGENT_POLICY_SCENARIO','--mount','type=bind,src=/srv/iot-fast/ci,dst=/srv/iot-fast/ci,readonly']
        if docker:
            for path in ['/usr/bin/docker','/var/run/docker.sock','/run/k3s/containerd/containerd.sock']:
                extra+=['--mount','type=bind,src='+path+',dst='+path+(',readonly' if path=='/usr/bin/docker' else '')]
        self.control(phase,cluster=cluster,extra=extra,memory='2g')


def candidate(c,label):
    """Render the actual tested chart with current owned values and append the live ModelConfig without executing a deployment."""
    import yaml
    build=c.prior('build');history=c.history('iot-agents')
    if history['status']!='deployed':raise ValueError('Preserve an unfinished agent Helm release')
    values=json.loads(subprocess.check_output([c.helm,'get','values','iot-agents','-n','iot-agents','--all','-o','json'],timeout=30))
    BASE.SHARED.save(c.out/(label+'-values.json'),values)
    chart=BASE.chart_path(c,build,'iot-agents');renderer=str(ROOT/'scripts/render-agent-responses.py')
    rendered=subprocess.check_output([c.helm,'template','iot-agents',chart,'-n','iot-agents','-f',str(c.out/(label+'-values.json')),'--post-renderer',renderer],timeout=30)
    documents=[v for v in yaml.safe_load_all(rendered) if v]
    model=c.get('get','modelconfig','default-model-config','-n','iot-agents');BASE.SHARED.save(c.out/(label+'-model.json'),model)
    envelope=deployment_envelope(documents,model);(c.out/(label+'-rendered.yaml')).write_bytes(rendered)
    return envelope,sha256(rendered).hexdigest(),model['metadata']['uid']


def run(phase):
    """Use the retained release stages with an explicit context injection; old pipeline source and historical receipts remain intact."""
    BASE.Context=PolicyContext
    if phase not in {'policy','policy-host','admitted-deploy-host'}:return BASE.run(phase)
    c=PolicyContext();c.prior('build');BASE.SHARED.real_admission(c,900)
    with (c.out/(phase+'.attempt')).open('x') as stream:stream.write(datetime.now(timezone.utc).isoformat())
    if phase=='policy':c.native('policy-host',cluster=True)
    elif phase=='policy-host':
        c.log('policy-contracts',[sys.executable,'-B','-m','unittest','tests.test_agent_admission','tests.test_agent_admission_ci','-q'])
        envelope,rendered_hash,model_uid=candidate(c,'policy-candidate')
        if c.scenario=='unsafe-tool':
            # A counterfactual JSON edit only; it is never sent to kubectl or Helm.
            agent=next(v for v in envelope['resources'] if v['kind']=='SandboxAgent' and v['metadata']['name']=='iot-retrieval')
            agent['spec']['declarative']['tools'][0]['mcpServer']['toolNames'].append('start_pump')
        decision=evaluate(ROOT,c.deploy/'data/runtime/policy-assets-28/opa',envelope,c.out/'policy-decision')
        if not decision['allowed']:raise RuntimeError('OPA denied this candidate before any deployment command')
        if c.scenario!='production':raise RuntimeError('A synthetic counterfactual must never authorize production deployment')
        BASE.SHARED.save(c.out/'policy.json',{'schema_version':1,'phase':'policy','passed':True,**c.identity,'source_digest':c.source_digest,
            'scenario':c.scenario,'decision':decision,'rendered_manifest_sha256':rendered_hash,'model_uid':model_uid})
    else:
        receipt=c.policy_receipt();envelope,rendered_hash,model_uid=candidate(c,'policy-recheck')
        if rendered_hash!=receipt['rendered_manifest_sha256'] or model_uid!=receipt['model_uid']:raise ValueError('The admitted manifest/model identity changed')
        require_same_admitted_input(receipt['decision'],envelope)
        decision=evaluate(ROOT,c.deploy/'data/runtime/policy-assets-28/opa',envelope,c.out/'policy-recheck-decision')
        require_same_admitted_input(decision,envelope)
        # The original deployment procedure starts only after both policy checks and the exact-input comparison.
        BASE.deploy_host(c)
    c.source_check()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=['admission','test','test-host','build','build-host','policy','policy-host','deploy','admitted-deploy-host','verify','verify-host','rollback','rollback-host','archive'])
    run(p.parse_args().phase)
