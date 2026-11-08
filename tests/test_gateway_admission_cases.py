"""Keep original admission fixtures intact while declaring current gateway counterexamples."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from agents.admission import canonical, deployment_envelope
from agents.admission_cases import cases
from tests import test_agent_gateway_default as gateway_fixture

ROOT = Path(__file__).resolve().parents[1]


class GatewayAdmissionCaseTests(unittest.TestCase):
    """Case declarations are not OPA proof; the native evaluator must check each expected decision."""

    def test_legacy_seventy_cases_and_source_are_preserved(self):
        """Historical eleven-resource cases must not acquire current-only mutations or be changed in place."""
        original = json.loads((ROOT/'tests/fixtures/agent-admission-observed.json').read_bytes())
        before = canonical(original)
        self.assertEqual(len(cases(original)), 70)
        self.assertEqual(canonical(original), before)

    def test_gateway_release_declares_each_delegation_and_route_counterexample(self):
        """Exercise the actual Helm render and declare missing/wrong auth, route confusion and mixed-mode edits."""
        fixture = json.loads((ROOT/'tests/fixtures/agent-admission-observed.json').read_bytes())
        model = deepcopy(next(r for r in fixture['resources'] if r['kind'] == 'ModelConfig'))
        model['spec']['openAI']['baseUrl'] = 'http://iot-agentgateway-candidate.iot-agents.svc.cluster.local:18080/v1'
        observed = deployment_envelope(gateway_fixture.GatewayDefaultTests().documents(True), model)
        before = canonical(observed); declared = cases(observed)
        rows = {r['id']: r for r in declared}
        wanted = {'missing-delegation-credential', 'unapproved-delegation-credential',
            'inline-delegation-authorization', 'forged-delegation-lineage-header',
            'direct-mcp-reference-in-gateway-mode', 'gateway-mcp-role-path-swap',
            'external-gateway-mcp-destination', 'missing-selected-gateway-mcp', 'mixed-direct-model-gateway-tools'}
        self.assertTrue(wanted <= set(rows), wanted-set(rows))
        self.assertEqual(len(rows), len(declared))
        for name in wanted:
            self.assertFalse(rows[name]['expected_allowed'])
            self.assertNotEqual(canonical(rows[name]['input']), before)
        self.assertEqual(canonical(observed), before)


if __name__ == '__main__': unittest.main()
