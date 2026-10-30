"""Refuse cross-build and counterfactual policy receipts before the production deployment controller is reached."""
from copy import deepcopy
import unittest
from agents.admission_ci import require_policy_phase


class AgentAdmissionCiTests(unittest.TestCase):
    """An OPA allow result is necessary but insufficient without the actual build/source/configuration identity."""
    def test_only_the_exact_production_phase_can_authorize_deployment(self):
        """A denied/cross-build/negative-test receipt cannot become a production approval."""
        identity={'component':'retrieval','build_number':'3','source_commit':'a'*40,'release':'llm-retrieval-jenkins-3'}
        value={'schema_version':1,'phase':'policy','passed':True,**identity,'source_digest':'b'*64,'scenario':'production',
            'model_uid':'actual-model-uid','rendered_manifest_sha256':'c'*64,'decision':{'allowed':True,'gate_exit':0,'violations':[]}}
        self.assertEqual(require_policy_phase(value,identity,'b'*64),value)
        for field,new in [('schema_version',2),('phase','test'),('passed',False),('component','coordinator'),('build_number','2'),
            ('source_commit','d'*40),('source_digest','d'*64),('scenario','unsafe-tool'),('model_uid',''),('rendered_manifest_sha256','')]:
            changed=deepcopy(value);changed[field]=new
            with self.subTest(field=field),self.assertRaises(ValueError):require_policy_phase(changed,identity,'b'*64)
        for field,new in [('allowed',False),('gate_exit',1),('violations',['bad'])]:
            changed=deepcopy(value);changed['decision'][field]=new
            with self.subTest(field=field),self.assertRaises(ValueError):require_policy_phase(changed,identity,'b'*64)
