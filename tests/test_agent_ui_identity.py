"""Reject unintended UI identity-policy changes and preserve unrelated environment settings."""
from copy import deepcopy
import unittest
from infra.gateway.ui_identity import ui_identity_patch


class AgentUiIdentityTests(unittest.TestCase):
    """Check the one-header policy on a minimal real-shaped UI deployment."""
    def test_only_the_reviewed_header_is_added_and_input_is_unchanged(self):
        """Preserve existing environment values and return an idempotent result after the exact setting is present."""
        value={'metadata':{'name':'kagent-ui','namespace':'iot-agents','uid':'same-ui'},'spec':{'template':{'spec':{'containers':[
            {'name':'ui','image':'ghcr.io/kagent-dev/kagent/ui:0.10.3','env':[{'name':'PORT','value':'3000'}]}]}}}}
        before=deepcopy(value);patch=ui_identity_patch(value);self.assertEqual(value,before)
        self.assertEqual(patch[-1]['value'],[{'name':'PORT','value':'3000'},{'name':'KAGENT_ADDITIONAL_FORWARDED_HEADERS','value':'X-User-Id'}])
        value['spec']['template']['spec']['containers'][0]['env']=patch[-1]['value'];self.assertEqual(ui_identity_patch(value),[])
        value['spec']['template']['spec']['containers'][0]['env'][-1]['value']='Authorization,X-Other-Identity'
        with self.assertRaises(ValueError):ui_identity_patch(value)


if __name__=='__main__':unittest.main()
