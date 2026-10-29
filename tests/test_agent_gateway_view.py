"""Ensure the credential-attaching administrative viewer cannot become an open proxy or a foreign-origin write target."""
from pathlib import Path
import importlib.util,unittest
ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('agent_gateway_view',ROOT/'scripts/view-agent-gateway.py')
VIEW=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(VIEW)


class AgentGatewayViewTests(unittest.TestCase):
    """Exercise hostile browser/proxy inputs and the narrow live-chat method exception."""
    def test_native_ui_navigation_and_same_origin_chat_are_allowed(self):
        """Permit real assets, catalog reads and the UI's same-origin API POSTs."""
        self.assertTrue(VIEW.allowed_request('agents','127.0.0.1:18090',None,'/_next/app.js','GET'))
        self.assertTrue(VIEW.allowed_request('agents','127.0.0.1:18090','http://127.0.0.1:18090','/api/sessions','POST'))
        self.assertTrue(VIEW.allowed_request('agents','127.0.0.1:18090','http://127.0.0.1:18090','/agents/iot-agents/iot-coordinator/chat','POST'))
        self.assertTrue(VIEW.allowed_request('registry','localhost:18091',None,'/v0/agents','GET'))

    def test_foreign_origins_absolute_targets_and_write_methods_are_refused(self):
        """Do not attach the operator credential to a rebinding Host, external proxy URL or destructive UI method."""
        for role,host,origin,path,method in [
            ('agents','evil.example',None,'/','GET'),('agents','127.0.0.1:18090','https://evil.example','/api/sessions','POST'),
            ('agents','127.0.0.1:18090',None,'http://external.example/','GET'),('agents','127.0.0.1:18090',None,'//external.example/','GET'),
            ('agents','127.0.0.1:18090',None,'/api/agents','DELETE'),('agents','127.0.0.1:18090',None,'/admin','POST'),
            ('registry','127.0.0.1:18091',None,'/v0/apply','POST')]:
            with self.subTest(path=path,method=method):self.assertFalse(VIEW.allowed_request(role,host,origin,path,method))

    def test_app_protocol_headers_survive_without_forwarding_client_trust_fields(self):
        """A missing segment-prefetch header changes Next.js request hashes; preserve it while refusing credential/identity overrides."""
        original={'Next-Router-Segment-Prefetch':'/_tree','RSC':'1','Next-Action':'action-id','Content-Type':'text/plain',
                  'Host':'127.0.0.1:18090','Authorization':'client-secret','X-User-Id':'other-user',
                  'X-Forwarded-Host':'external.example','Connection':'keep-alive'}
        actual=VIEW.upstream_headers(original,'agents.iot.test','operator-login')
        self.assertEqual(actual,{'Next-Router-Segment-Prefetch':'/_tree','RSC':'1','Next-Action':'action-id','Content-Type':'text/plain',
                                 'Host':'agents.iot.test','Authorization':'operator-login','Origin':'https://agents.iot.test'})


if __name__=='__main__':unittest.main()
