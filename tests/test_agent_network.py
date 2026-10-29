"""Exercise security-sensitive policy changes and reject broadening beyond the dedicated worker roles."""
import copy
import importlib.util
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('agent_network', ROOT / 'scripts/configure-agent-network.py')
network = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(network)
PROBE_SPEC = importlib.util.spec_from_file_location('agent_network_probe', ROOT / 'scripts/probe-agent-network.py')
probe = importlib.util.module_from_spec(PROBE_SPEC); PROBE_SPEC.loader.exec_module(probe)


def reviewed_documents():
    """Expand only the chart's two simple role directives into fixture policies without a Helm dependency."""
    text = (ROOT / 'infra/charts/iot-agent-network/templates/network.yaml').read_text()
    documents = []
    for role in sorted(network.ROLES):
        body = text.split('{{- range')[1].split('}}', 1)[1].rsplit('{{- end }}', 1)[0]
        start = body.index('{{- if'); finish = body.index('{{- end }}', start)
        inner = body[start:finish].split('}}', 1)[1]
        body = body[:start] + (inner if role != 'coordinator' else '') + body[finish + len('{{- end }}'):]
        body = body.replace('{{ $role }}', role)
        begin = body.index('{{ required'); end = body.index('}}', begin)
        body = body[:begin] + '"10.42.0.2/32"' + body[end + 2:]
        documents.extend(yaml.safe_load_all(body))
    return documents


class AgentNetworkTests(unittest.TestCase):
    """Use negative permission cases as the main policy correctness contract."""

    def test_chart_keeps_three_distinct_worker_boundaries(self):
        """Each specialist keeps its MCP destination; coordinator uses delegation only."""
        self.assertTrue(network.validate_policies(reviewed_documents() + [None], '10.42.0.2/32'))

    def test_broad_or_public_api_destinations_are_rejected(self):
        """A control-plane exception cannot silently become public or encompass a subnet."""
        for value in ['0.0.0.0/0', '10.42.0.0/24', '8.8.8.8/32', '::1/128', '10.42.0.2/24']:
            with self.subTest(value=value), self.assertRaises(ValueError): network.private_control_cidr(value)

    def test_accidental_permission_expansion_is_rejected(self):
        """Catch opposite-role access, open ports, empty selectors and additive broad allow rules."""
        base = reviewed_documents()
        def mutate(case, documents):
            """Apply one independently unsafe edit to the drift policy."""
            spec = documents[1]['spec']
            if case == 'opposite': spec['egress'][-1]['to'][0]['podSelector']['matchLabels']['app.kubernetes.io/instance'] = 'iot-retrieval-mcp'
            elif case == 'ports': spec['egress'][-1].pop('ports')
            elif case == 'namespace': spec['egress'][-1]['to'][0]['namespaceSelector'] = {}
            elif case == 'pods': spec['podSelector'] = {}
            elif case == 'extra': spec['egress'].append({})
            elif case == 'ingress': spec['policyTypes'].append('Ingress')
        for case in ['opposite', 'ports', 'namespace', 'pods', 'extra', 'ingress']:
            changed = copy.deepcopy(base); mutate(case, changed)
            with self.subTest(case=case), self.assertRaises(ValueError): network.validate_policies(changed, '10.42.0.2/32')

    def test_coordinator_cannot_receive_direct_mcp_permission(self):
        """The coordinator must call specialist agents rather than either underlying MCP service."""
        documents = reviewed_documents()
        coordinator = next(d for d in documents if d['metadata']['name'] == 'iot-coordinator-worker-egress')
        specialist = next(d for d in documents if d['metadata']['name'] == 'iot-retrieval-worker-egress')
        coordinator['spec']['egress'].append(copy.deepcopy(specialist['spec']['egress'][-1]))
        with self.assertRaises(ValueError): network.validate_policies(documents, '10.42.0.2/32')

    def test_timeout_alone_and_failed_controls_never_establish_denial(self):
        """Require the network counter and an independently successful reversible control."""
        diagnostic = {'allowed_mcp': {'http_status': 200}, 'filesystem_boundary_verified': True,
                      'blocked_other_role_mcp': {'http_status': 0, 'error_type': '*url.Error'}}
        self.assertFalse(probe.phase_accepted('deny-before', diagnostic, 0))
        self.assertTrue(probe.phase_accepted('deny-before', diagnostic, 3))
        self.assertFalse(probe.phase_accepted('allow-control', diagnostic, 3))
        diagnostic['blocked_other_role_mcp'] = {'http_status': 200}
        self.assertTrue(probe.phase_accepted('allow-control', diagnostic, 0))
        self.assertFalse(probe.phase_accepted('allow-control', diagnostic, 1))
        self.assertFalse(probe.phase_accepted('deny-restored', diagnostic, 3))

    def test_packet_counter_is_bound_to_the_observed_source_worker(self):
        """Exclude another worker and avoid counting duplicate hook references to the same chain twice."""
        raw = b'''[1:64] -A FORWARD -s 10.52.0.84/32 -j KUBE-POD-FW-OWN
[1:64] -A OUTPUT -s 10.52.0.84/32 -j KUBE-POD-FW-OWN
[1:64] -A FORWARD -s 10.52.0.83/32 -j KUBE-POD-FW-OTHER
[3:192] -A KUBE-POD-FW-OWN -m mark ! --mark 0x10000/0x10000 -j REJECT
[90:5760] -A KUBE-POD-FW-OTHER -j REJECT
'''
        self.assertEqual(probe.reject_counters(raw, '10.52.0.84')['packets'], 3)
        with self.assertRaises(ValueError): probe.reject_counters(raw, '10.52.0.99')
        with self.assertRaises(ValueError): probe.reject_counters(raw.split(b'[3:192]')[0], '10.52.0.84')

    def test_actor_name_handles_both_short_and_hashed_native_ids(self):
        """Retrieval and drift context IDs cross different Kubernetes name-length boundaries."""
        self.assertEqual(probe.actor_id('retrieval', '562e9452-bcdd-4a71-a59a-d39dfecb31df'), 'asr-936a644d04c18bef58c3c132')
        self.assertEqual(probe.actor_id('drift', 'b32f6ee6-c94a-4731-97b0-cfe614f59d38'), 'asr-iot-agents-iot-drift-b32f6ee6-c94a-4731-97b0-cfe614f59d38')


if __name__ == '__main__': unittest.main()
