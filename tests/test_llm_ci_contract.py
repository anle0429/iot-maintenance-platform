"""Reject crossed CI receipts, source escapes and capability regressions before a native release is deployed."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import importlib.util
import json
import tempfile
import unittest
from agents.ci_contract import release_identity, require_receipt, validate_agents, validate_source

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('ci_response_renderer',ROOT/'scripts/render-agent-responses.py')
renderer=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(renderer)


class LlmCiContractTests(unittest.TestCase):
    """Use recorded resource fixtures and independent small seals; live CI still needs its own native proof."""

    def test_four_jobs_have_distinct_release_names(self):
        """The same build number in two jobs must not share a release or successful-stage receipt."""
        identities=[release_identity(role,'1','a'*40) for role in ['rag','retrieval','drift','coordinator']]
        self.assertEqual(len({value['release'] for value in identities}),4)
        receipt={'schema_version':1,'phase':'test','passed':True,**identities[0]}
        self.assertEqual(require_receipt(receipt,identities[0],'test'),receipt)
        with self.assertRaises(ValueError):require_receipt(receipt,identities[1],'test')
        with self.assertRaises(ValueError):require_receipt(receipt,identities[0],'build')

    def test_malformed_jobs_builds_commits_and_stale_receipts_fail(self):
        """Inputs cannot escape output paths or inherit another build's success."""
        for component,build,commit in [('shell','1','a'*40),('rag','../1','a'*40),('rag',True,'a'*40),('rag','0','a'*40),('rag','1','short')]:
            with self.subTest(component=component,build=build),self.assertRaises(ValueError):release_identity(component,build,commit)
        identity=release_identity('rag',1,'a'*40)
        for changed in [{'build_number':'2'},{'source_commit':'b'*40},{'passed':False},{'schema_version':0}]:
            receipt={'schema_version':1,'phase':'test','passed':True,**identity,**changed}
            with self.subTest(changed=changed),self.assertRaises(ValueError):require_receipt(receipt,identity,'test')

    def test_exact_source_then_mutation_and_traversal(self):
        """A valid hash outside the workspace and a changed in-tree file must both fail."""
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'tree';root.mkdir();(root/'code.py').write_bytes(b'original')
            seal={'format':'iot-sealed-llm-ci-v1','commit':'a'*40,'files':{'code.py':sha256(b'original').hexdigest()}}
            self.assertEqual(len(validate_source(root,seal,'a'*40)),64)
            (root/'code.py').write_bytes(b'changed')
            with self.assertRaises(ValueError):validate_source(root,seal,'a'*40)
            (root.parent/'outside').write_bytes(b'original');seal['files']={'../outside':sha256(b'original').hexdigest()}
            with self.assertRaises(ValueError):validate_source(root,seal,'a'*40)

    def test_private_runtime_files_are_not_ci_source(self):
        """Even correctly hashed local state must not enter the packaged Git/CI source."""
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for name in ['data/token','outputs/result.json','AGENTS.md','private.key']:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'local')
                seal={'format':'iot-sealed-llm-ci-v1','commit':'a'*40,'files':{name:sha256(b'local').hexdigest()}}
                with self.subTest(name=name),self.assertRaises(ValueError):validate_source(root,seal,'a'*40)

    def test_reviewed_response_render_is_accepted(self):
        """Use the actual historical objects and current policy, without treating them as a new deployment."""
        original=json.loads((ROOT/'docs/evidence/agent-platform/readback/agents.stdout').read_bytes())['items']
        policy=(ROOT/'agents/response-policy.json').read_bytes()
        self.assertTrue(validate_agents(renderer.render(original,policy),json.loads(policy)))

    def test_prompt_tools_model_network_or_delegation_cannot_regress(self):
        """A valid chart is rejected when it downgrades policy, adds a tool or points outside its reviewed scope."""
        original=json.loads((ROOT/'docs/evidence/agent-platform/readback/agents.stdout').read_bytes())['items']
        policy=(ROOT/'agents/response-policy.json').read_bytes();valid=renderer.render(original,policy)
        for case in ['old-version','prompt','extra-tool','external-model','network','cross-namespace','delegation']:
            docs=deepcopy(valid);agent=next(a for a in docs if a['metadata']['name']=='iot-retrieval');spec=agent['spec']
            if case=='old-version':spec['version']='0.1.0'
            elif case=='prompt':spec['declarative']['systemMessage']='A different instruction'
            elif case=='extra-tool':spec['declarative']['tools'].append({'type':'shell'})
            elif case=='external-model':spec['declarative']['modelConfig']='external'
            elif case=='network':spec['sandbox']['network']['allowedDomains'].append('*')
            elif case=='cross-namespace':agent['metadata']['namespace']='default'
            else:next(a for a in docs if a['metadata']['name']=='iot-coordinator')['spec']['declarative']['tools'][0]['isolateSessions']=False
            with self.subTest(case=case),self.assertRaises(ValueError):validate_agents(docs,json.loads(policy))


if __name__=='__main__':unittest.main()
