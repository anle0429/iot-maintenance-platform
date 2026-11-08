"""Refuse cross-build and counterfactual policy receipts before the production deployment controller is reached."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock
import importlib.util
import unittest
from agents.admission_ci import require_policy_phase,production_agents


class AgentAdmissionCiTests(unittest.TestCase):
    """An OPA allow result is necessary but insufficient without the actual build/source/configuration identity."""

    def test_policy_and_test_containers_receive_the_readonly_helm_executable(self):
        """The real policy contract suite renders Helm charts before OPA and needs the same bounded binary mount."""
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('policy_control_mount_fixture',root/'scripts/ci-agents-policy.py')
        driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
        context=driver.PolicyContext.__new__(driver.PolicyContext)
        context.helm='/retained/pinned-assets/helm';context.control=Mock()
        expected='type=bind,src='+context.helm+',dst=/usr/local/bin/helm,readonly'
        for phase in ['policy-host','test-host']:
            context.control.reset_mock()
            context.native(phase,cluster=True)
            args,kwargs=context.control.call_args
            self.assertEqual(args,(phase,));self.assertTrue(kwargs['cluster'])
            extra=kwargs['extra']
            mounts=[extra[index+1] for index,value in enumerate(extra[:-1]) if value=='--mount']
            with self.subTest(phase=phase):self.assertEqual(mounts.count(expected),1)

    def test_retained_experiments_are_explicit_and_never_hide_unknown_agents(self):
        """The accepted A/B pair does not change the three-role release, while incomplete or unfamiliar extras fail."""
        def agent(name):
            """Represent an independent namespace/type identity; readiness remains the retained controller's responsibility."""
            return {'kind':'SandboxAgent','metadata':{'name':name,'namespace':'iot-agents'}}
        baseline=[agent('iot-'+r) for r in ['retrieval','drift','coordinator']]
        pair=[agent('iot-ab-retrieval-'+v) for v in ['a','b']];all_agents=deepcopy(baseline+pair)
        self.assertEqual(production_agents(baseline),baseline);self.assertEqual(production_agents(all_agents),baseline)
        self.assertEqual(all_agents,baseline+pair)
        for bad in [baseline[:-1],baseline+[pair[0]],baseline+[agent('unreviewed')],baseline+[baseline[0]]]:
            with self.subTest(items=bad),self.assertRaises(ValueError):production_agents(bad)

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
