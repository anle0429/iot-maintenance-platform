"""Check child A2A transport attribution against selected histories and actual worker identities."""
from copy import deepcopy
import unittest

from agents.gateway_acceptance import verify_delegation_paths


PARENT = '11111111-1111-4111-8111-111111111111'
CHILDREN = {'retrieval': '22222222-2222-4222-8222-222222222222',
            'drift': '33333333-3333-4333-8333-333333333333'}
OTHER = '44444444-4444-4444-8444-444444444444'
WORKERS = {'10.42.0.17': {'role': 'coordinator', 'pod': 'coordinator-worker-1', 'uid': 'actual-worker-uid'},
           '10.42.0.18': {'role': 'retrieval', 'pod': 'retrieval-worker-1', 'uid': 'retrieval-worker-uid'}}


def record(role, *, slash=False):
    """Represent the exact flat fields emitted by the native JSON access logger."""
    return {'route': 'listener/0/delegate-' + role + ('-slash' if slash else ''),
            'http.method': 'POST', 'http.path': '/api/a2a-sandboxes/iot-agents/iot-' + role + ('/' if slash else ''),
            'http.status': 200, 'src.addr': '10.42.0.17:43123', 'a2a.method': 'message/send',
            'a2a.response.outcome': 'success', 'a2a.result.kind': 'task', 'a2a.task.state': 'completed',
            'a2a.context.id': CHILDREN[role], 'a2a_parent_context_id': PARENT,
            'a2a_root_context_id': PARENT, 'a2a_source': 'agent'}


class GatewayDelegationAcceptanceTests(unittest.TestCase):
    """Transport evidence must bind both actual child sessions to one coordinator parent."""

    def verify(self, records, *, children=None, workers=None):
        """Use independent selected child identities and worker inventory for every check."""
        return verify_delegation_paths(records, PARENT, CHILDREN if children is None else children,
                                       WORKERS if workers is None else workers)

    def test_accepts_both_exact_paths_and_joins_actual_coordinator_worker(self):
        """Canonical/slash routes count while unrelated ingress/model/MCP/card logs do not."""
        records = [record('retrieval'), record('drift', slash=True)]
        records[0]['agent_role'] = 'incorrect heuristic label'
        unrelated = record('retrieval')
        unrelated.update(a2a_parent_context_id=OTHER, a2a_root_context_id=OTHER)
        unrelated['a2a.context.id'] = OTHER
        discovery = record('retrieval')
        discovery.update({'http.method': 'GET', 'http.path': discovery['http.path'] + '/.well-known/agent-card.json'})
        discovery.pop('a2a.method')
        records += [unrelated, discovery,
                    {'route': 'listener/model', 'http.method': 'POST', 'a2a_parent_context_id': PARENT},
                    {'route': 'listener/proxy-mcp-retrieval', 'http.method': 'POST', 'a2a_parent_context_id': PARENT},
                    {'route': 'listener/a2a-coordinator', 'http.method': 'POST', 'a2a.method': 'message/send',
                     'a2a.context.id': PARENT}]
        original = deepcopy(records)
        result = self.verify(records)
        self.assertTrue(result['passed'])
        self.assertTrue(result['delegation_transport_verified'])
        self.assertEqual(result['parent_context_id'], PARENT)
        self.assertEqual(set(result['children']), {'retrieval', 'drift'})
        for role in CHILDREN:
            self.assertEqual(result['children'][role]['context_id'], CHILDREN[role])
            self.assertEqual(result['children'][role]['worker'], {'ip': '10.42.0.17', **WORKERS['10.42.0.17']})
        self.assertEqual(records, original)
        self.assertIn('MCP', result['scope'])

    def test_requires_exact_distinct_child_uuids_separate_from_parent(self):
        """Malformed, extra, duplicated and parent-as-child identities cannot be accepted."""
        for children in [None, {}, {**CHILDREN, 'other': OTHER},
                         {**CHILDREN, 'retrieval': 'not-a-uuid'},
                         {**CHILDREN, 'retrieval': PARENT},
                         {**CHILDREN, 'retrieval': CHILDREN['drift']}]:
            with self.subTest(children=children), self.assertRaises(ValueError):
                verify_delegation_paths([record('retrieval'), record('drift')], PARENT, children, WORKERS)
        with self.assertRaises(ValueError):
            verify_delegation_paths([record('retrieval'), record('drift')], 'not-a-uuid', CHILDREN, WORKERS)

    def test_http_success_cannot_replace_completed_successful_task(self):
        """HTTP errors, failed tasks, wrong result kinds and different RPCs all fail acceptance."""
        for field, value in [('http.status', 503), ('a2a.response.outcome', 'error'),
                             ('a2a.result.kind', 'message'), ('a2a.task.state', 'failed'),
                             ('http.method', 'GET'), ('a2a.method', 'tasks/get')]:
            rows = [record('retrieval'), record('drift')]
            rows[0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.verify(rows)

    def test_rejects_wrong_lineage_source_child_route_or_path(self):
        """A matched parent must identify the exact child, source marker and approved endpoint."""
        for field, value in [('a2a_parent_context_id', OTHER), ('a2a_root_context_id', OTHER),
                             ('a2a.context.id', OTHER), ('a2a_source', 'operator'),
                             ('route', 'listener/delegate-coordinator'), ('route', 'listener/unreviewed'),
                             ('route', 'listener/a2a-retrieval'),
                             ('http.path', '/api/a2a-sandboxes/iot-agents/iot-drift'),
                             ('http.path', '/api/a2a-sandboxes/iot-agents/iot-retrieval/')]:
            rows = [record('retrieval'), record('drift')]
            rows[0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.verify(rows)

    def test_unknown_or_noncoordinator_source_ip_cannot_use_heuristic_role(self):
        """Only an inventoried coordinator Pod IP with actual Pod UID can identify the caller."""
        for address in ['10.42.0.18:43123', '10.42.0.99:43123', '10.42.0.17', '10.42.0.17:invalid']:
            rows = [record('retrieval'), record('drift')]
            rows[0].update({'src.addr': address, 'agent_role': 'coordinator'})
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.verify(rows)
        workers = deepcopy(WORKERS)
        workers['10.42.0.17'].pop('uid')
        with self.assertRaises(ValueError):
            self.verify([record('retrieval'), record('drift')], workers=workers)

    def test_duplicate_and_missing_role_are_rejected(self):
        """One successful canonical and slash call for the same child is still a duplicate."""
        for rows in [[record('retrieval')], [record('drift')],
                     [record('retrieval'), record('drift'), record('retrieval')],
                     [record('retrieval'), record('drift'), record('retrieval', slash=True)]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.verify(rows)

    def test_extra_matched_parent_unknown_child_role_or_source_is_rejected(self):
        """Two good paths cannot conceal another attributed call to an unverified destination."""
        for change in [{'a2a.context.id': OTHER}, {'route': 'listener/delegate-unknown'},
                       {'a2a_source': 'unknown'}, {'a2a.task.state': 'failed'}]:
            extra = record('retrieval')
            extra.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.verify([record('retrieval'), record('drift'), extra])

    def test_bounded_lineage_fields_cannot_be_missing(self):
        """Old logs without the new parent/root/source fields do not establish this transport claim."""
        for field in ['a2a_parent_context_id', 'a2a_root_context_id', 'a2a_source']:
            rows = [record('retrieval'), record('drift')]
            rows[0].pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(rows)

    def test_internal_lineage_cannot_hide_a_coordinator_self_call_as_parent_ingress(self):
        """An extra self-route with either matching child-lineage header is not operator ingress."""
        for fields in [{'a2a_parent_context_id': PARENT}, {'a2a_root_context_id': PARENT},
                       {'a2a_parent_context_id': PARENT, 'a2a_root_context_id': PARENT}]:
            extra = {'route': 'listener/a2a-coordinator', 'http.method': 'POST',
                     'http.path': '/api/a2a-sandboxes/iot-agents/iot-coordinator',
                     'http.status': 200, 'a2a.method': 'message/send', 'a2a.context.id': PARENT,
                     'a2a.response.outcome': 'success', 'a2a.result.kind': 'task',
                     'a2a.task.state': 'completed', 'a2a_source': 'agent', **fields}
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.verify([record('retrieval'), record('drift'), extra])


if __name__ == '__main__':
    unittest.main()
