"""Exercise fail-closed policy result parsing and configuration binding without imitating the Rego policy implementation."""
from copy import deepcopy
from hashlib import sha256
import unittest
from agents.admission import canonical,deployment_envelope,decision_value,require_same_admitted_input


class AgentAdmissionTests(unittest.TestCase):
    """Missing decisions and time-of-check configuration edits must not authorize deployment."""
    def test_complete_specs_and_unknown_kinds_reach_the_policy(self):
        """An unknown resource cannot disappear from admission, and normalizing metadata must not edit the render."""
        body={'apiVersion':'example/v1','kind':'Unexpected','metadata':{'name':'extra'},'spec':{'extra':True}}
        original=deepcopy(body);value=deployment_envelope([body,None],body)
        self.assertEqual(body,original);self.assertEqual(len(value['resources']),2)
        self.assertEqual(value['resources'][0]['kind'],'Unexpected');self.assertEqual(value['resources'][0]['spec'],{'extra':True})
        self.assertEqual(value['resources'][0]['metadata']['namespace'],'iot-agents')
        with self.assertRaises(ValueError):deployment_envelope([{'metadata':None}],body)

    def test_only_an_explicit_boolean_decision_can_pass(self):
        """Exit zero without a defined Boolean result is insufficient; errors and empty results fail closed."""
        for decision in [{'allowed':True,'violations':[]},{'allowed':False,'violations':['unapproved tool']}]:
            self.assertEqual(decision_value({'result':[{'expressions':[{'value':decision}]}]}),decision)
        for payload in [{},{'result':[]},{'result':[{},{}]},{'result':[{'expressions':[]}]},
            {'result':[{'expressions':[{'value':{'allowed':1,'violations':[]}}]}]},
            {'result':[{'expressions':[{'value':{'allowed':True,'violations':['unapproved tool']}}]}]},
            {'result':[{'expressions':[{'value':{'allowed':False,'violations':[]}}]}]}]:
            with self.subTest(payload=payload),self.assertRaises(ValueError):decision_value(payload)

    def test_deployment_cannot_use_a_stale_or_denied_receipt(self):
        """The same allow decision binds one exact configuration, including nested model/tool destinations."""
        value={'resources':[{'spec':{'url':'http://approved.internal'}}]}
        receipt={'allowed':True,'gate_exit':0,'violations':[],'input_sha256':sha256(canonical(value)).hexdigest()}
        require_same_admitted_input(receipt,value)
        changed=deepcopy(value);changed['resources'][0]['spec']['url']='https://unapproved.example.invalid'
        with self.assertRaises(ValueError):require_same_admitted_input(receipt,changed)
        for field,new in [('allowed',False),('gate_exit',1),('violations',['bad']),('input_sha256','0'*64)]:
            bad={**receipt,field:new}
            with self.subTest(field=field),self.assertRaises(ValueError):require_same_admitted_input(bad,value)
        with self.assertRaises(ValueError):canonical({'tokens':float('nan')})
