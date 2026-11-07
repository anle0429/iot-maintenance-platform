"""Render the selected gateway route and reject CI reapplication that silently bypasses it."""
from copy import deepcopy
from pathlib import Path
import importlib.util,json,subprocess,unittest
import yaml
from agents.ci_contract import validate_agents

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('gateway_response_renderer',ROOT/'scripts/render-agent-responses.py')
RENDERER=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(RENDERER)


class GatewayDefaultTests(unittest.TestCase):
    """Require the exact reviewed tools and domains under both explicitly selected deployment modes."""
    def documents(self,gateway):
        """Use real Helm plus the actual versioned prompt renderer, rather than hand-built matching fixtures."""
        raw=subprocess.check_output(['helm','template','iot-agents',str(ROOT/'infra/charts/iot-agents'),'-n','iot-agents',
             '--set','agentgateway.enabled='+str(gateway).lower()])
        return RENDERER.render(yaml.safe_load_all(raw),(ROOT/'agents/response-policy.json').read_bytes())

    def test_gateway_selection_is_explicit_and_contains_both_authenticated_mcp_routes(self):
        """Both selected specialists use stable gateway references while the original direct resources remain available."""
        docs=self.documents(True);policy=json.loads((ROOT/'agents/response-policy.json').read_bytes())
        self.assertTrue(validate_agents(docs,policy,gateway=True))
        tools={d['metadata']['name']:d['spec'] for d in docs if d and d['kind']=='RemoteMCPServer'}
        self.assertEqual(tools['iot-gateway-retrieval-tools']['url'],'http://iot-agentgateway-candidate.iot-agents.svc.cluster.local:18081/mcp')
        self.assertEqual(tools['iot-gateway-drift-tools']['url'],'http://iot-agentgateway-candidate.iot-agents.svc.cluster.local:18082/mcp')
        self.assertEqual(tools['iot-gateway-retrieval-tools']['headersFrom'],tools['iot-retrieval-tools']['headersFrom'])

    def test_selected_mode_cannot_silently_fall_back_to_direct_tools(self):
        """A direct chart render is not a valid gateway release, and an unknown tool destination remains rejected."""
        docs=self.documents(False);policy=json.loads((ROOT/'agents/response-policy.json').read_bytes())
        self.assertTrue(validate_agents(docs,policy))
        with self.assertRaises(ValueError):validate_agents(docs,policy,gateway=True)
        docs=self.documents(True)
        target=next(d for d in docs if d and d['kind']=='SandboxAgent' and d['metadata']['name']=='iot-retrieval')
        target['spec']['declarative']['tools'][0]['mcpServer']['name']='arbitrary-server'
        with self.assertRaises(ValueError):validate_agents(docs,policy,gateway=True)


if __name__=='__main__':unittest.main()
