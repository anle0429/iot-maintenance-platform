"""Test the independent review verifier with explicit driver fixtures and original child-history shapes."""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import importlib.util
import json
import sys
import tarfile
import unittest

from agents.admission import canonical, deployment_envelope
from agents.admission_cases import cases
from tests import test_agent_gateway_default as gateway_fixture


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('agent_delegation_review', ROOT/'scripts/verify-agent-delegation-review.py')
CHECK = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(CHECK)


def encoded(value):
    """Serialize an in-memory test object; these fixture bytes are never native execution evidence."""
    return (json.dumps(value, sort_keys=True) + '\n').encode()


def policy_fixture():
    """Build a driver-only fixture from the real chart and source case declarations, without running OPA."""
    old = json.loads((ROOT/'tests/fixtures/agent-admission-observed.json').read_bytes())
    model = deepcopy(next(r for r in old['resources'] if r['kind'] == 'ModelConfig'))
    model['spec']['openAI']['baseUrl'] = 'http://iot-agentgateway-candidate.iot-agents.svc.cluster.local:18080/v1'
    observed = deployment_envelope(gateway_fixture.GatewayDefaultTests().documents(True), model)
    declared = cases(observed)
    names = ['agents/admission.py', 'agents/admission_cases.py', 'agents/policy_execution.py',
             'scripts/check-agent-admission.py', 'infra/policy/agent-admission.rego',
             'infra/policy/agent-admission-approved.json', 'agents/response-policy.json', 'infra/policy/opa-assets.json']
    files = {'source/tree/' + name: (ROOT/name).read_bytes() for name in names}
    seal = {'format': 'iot-agent-admission-source-v1', 'commit': 'a'*40,
            'files': {name: sha256(files['source/tree/' + name]).hexdigest() for name in names}}
    files['policy/source.json'] = encoded(seal)
    files['policy/observed.json'] = canonical(observed)
    assets = json.loads(files['source/tree/infra/policy/opa-assets.json'])
    pins = {name: seal['files'][name] for name in
            ['infra/policy/agent-admission.rego', 'infra/policy/agent-admission-approved.json', 'agents/response-policy.json']}
    files['policy/opa-check.stdout'] = b''
    files['policy/opa-check.stderr'] = b''
    files['policy/opa-version.txt'] = ('Version: ' + assets['version'] + '\nSynthetic verifier fixture only\n').encode()
    files['policy/opa-version.stderr'] = b''
    invocations = []

    def invocation(argv, code, stdout_path, stderr_path):
        """Describe a mocked completed command solely for offline verifier mutation tests."""
        invocations.append({'sequence': len(invocations)+1, 'verb': argv[1], 'argv': argv, 'exit_code': code,
            'stdout_sha256': sha256(files[stdout_path]).hexdigest(), 'stderr_sha256': sha256(files[stderr_path]).hexdigest()})

    invocation(['/runtime/opa', 'check', '--strict', '/source/infra/policy/agent-admission.rego'], 0,
               'policy/opa-check.stdout', 'policy/opa-check.stderr')
    invocation(['/runtime/opa', 'version'], 0, 'policy/opa-version.txt', 'policy/opa-version.stderr')
    rows = []
    for case in declared:
        name = 'policy/' + case['id'] + '/'
        allowed = case['expected_allowed']
        decision = {'allowed': allowed, 'violations': [] if allowed else ['synthetic verifier fixture denial']}
        files[name+'input.json'] = canonical(case['input'])
        files[name+'decision.stdout'] = encoded({'result': [{'expressions': [{'value': decision}]}]})
        files[name+'decision.stderr'] = b''
        files[name+'decision-exit.json'] = encoded({'exit_code': 0})
        files[name+'gate.stdout'] = encoded({'result': [{'expressions': [{'value': True}]}]} if allowed else {})
        files[name+'gate.stderr'] = b''
        files[name+'gate-exit.json'] = encoded({'exit_code': 0 if allowed else 1})
        report = {**decision, 'gate_exit': 0 if allowed else 1, 'checked_utc': 'synthetic unit fixture',
                  'input_sha256': sha256(files[name+'input.json']).hexdigest(), 'policy_files': pins,
                  'opa_sha256': assets['sha256'], 'opa_version': assets['version']}
        files[name+'verification.json'] = encoded(report)
        rows.append({'case': case['id'], 'expected_allowed': allowed, **report})
        base = ['/runtime/opa', 'eval', '--strict', '--strict-builtin-errors', '--input',
                '/output/policy/' + case['id'] + '/input.json']
        for policy in pins:
            base += ['--data', '/source/' + policy]
        invocation(base + ['--format=json', 'data.iot.agent_admission.result'], 0,
                   name+'decision.stdout', name+'decision.stderr')
        invocation(base + ['--format=json', '--fail', 'data.iot.agent_admission.result.allowed = true'],
                   0 if allowed else 1, name+'gate.stdout', name+'gate.stderr')
    ledger = {'pinned_opa_path': '/runtime/opa', 'pinned_opa_sha256': assets['sha256'],
              'allowed_verbs': ['check', 'eval', 'version'], 'invocation_count': len(invocations),
              'verb_counts': dict(Counter(row['verb'] for row in invocations)), 'invocations': invocations,
              'scope': 'Synthetic unit fixture; no OPA, deployment or model execution.'}
    files['policy/process-ledger.json'] = encoded(ledger)
    files['policy/verification.json'] = encoded({'passed': True, 'source_commit': seal['commit'], 'cases': rows,
        'allowed_cases': sum(row['allowed'] for row in rows), 'denied_cases': sum(not row['allowed'] for row in rows),
        'post_admission_tool_edit_rejected': True, 'resource_count': len(observed['resources']),
        'observed_input_sha256': sha256(canonical(observed)).hexdigest(),
        'denied_receipt_bindings_rejected': [row['case'] for row in rows if not row['allowed']],
        'process_ledger': ledger})
    return files, [row['id'] for row in declared]


class PolicyReviewEvidenceTests(unittest.TestCase):
    """Source declarations determine the expected cases; fixture replies test verification plumbing only."""

    @classmethod
    def setUpClass(cls):
        """Prepare one explicitly synthetic suite from the real current thirteen-resource render."""
        cls.files, cls.identifiers = policy_fixture()

    def verify(self, files):
        """Require the explicit expected identifiers and exact source snapshot for this suite."""
        return CHECK.verify_policy_suite(files, 'policy/', expected_case_ids=self.identifiers, source_prefix='source/tree/')

    def test_declared_gateway_suite_and_every_recorded_stream_are_reconciled(self):
        """The current generator declares79cases and the driver emits2+2Nprocess records."""
        result = self.verify(self.files)
        self.assertEqual(result['case_count'], len(self.identifiers))
        self.assertEqual(result['case_count'], 79)
        self.assertEqual(result['allowed_cases'], 4)
        self.assertEqual(result['denied_cases'], 75)
        self.assertEqual(result['opa_command_count'], 2 + 2*len(self.identifiers))
        self.assertNotIn('model_requests', result)

    def test_missing_or_rehashed_changed_case_input_is_rejected(self):
        """Rehashing altered native input cannot replace the source generator's declared mutation."""
        key = 'policy/unsafe-hardware-tool/input.json'
        for mode in ['missing', 'changed']:
            files = dict(self.files)
            if mode == 'missing':
                files.pop(key)
            else:
                value = json.loads(files[key]); value['format'] = 'undeclared'
                files[key] = canonical(value)
                row = json.loads(files['policy/unsafe-hardware-tool/verification.json'])
                row['input_sha256'] = sha256(files[key]).hexdigest()
                files['policy/unsafe-hardware-tool/verification.json'] = encoded(row)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.verify(files)

    def test_decisions_exits_stream_hashes_and_report_counts_are_independent(self):
        """Neither an edited summary nor re-labeled process record can override original raw outputs."""
        for mode in ['decision', 'exit', 'stream', 'count', 'command', 'version-stderr']:
            files = dict(self.files)
            if mode == 'decision':
                files['policy/unsafe-hardware-tool/decision.stdout'] = encoded({'result': [{'expressions': [
                    {'value': {'allowed': True, 'violations': []}}]}]})
            elif mode == 'exit':
                files['policy/unsafe-hardware-tool/gate-exit.json'] = encoded({'exit_code': 0})
            elif mode == 'version-stderr':
                files.pop('policy/opa-version.stderr')
            else:
                report = json.loads(files['policy/verification.json'])
                if mode == 'count':
                    report['denied_cases'] -= 1
                elif mode == 'stream':
                    report['process_ledger']['invocations'][2]['stdout_sha256'] = '0'*64
                else:
                    report['process_ledger']['invocations'][2]['argv'][0] = '/runtime/helm'
                files['policy/verification.json'] = encoded(report)
                files['policy/process-ledger.json'] = encoded(report['process_ledger'])
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.verify(files)

    def test_source_replacement_and_different_expected_inventory_are_rejected(self):
        """The expected source and explicit case list cannot silently drift with the receipts."""
        files = dict(self.files)
        name = 'agents/admission_cases.py'
        files['source/tree/'+name] += b'\n# unreviewed source replacement\n'
        seal = json.loads(files['policy/source.json'])
        seal['files'][name] = sha256(files['source/tree/'+name]).hexdigest()
        files['policy/source.json'] = encoded(seal)
        with self.assertRaises(ValueError):
            self.verify(files)
        with self.assertRaises(ValueError):
            CHECK.verify_policy_suite(self.files, 'policy/', expected_case_ids=self.identifiers[:-1], source_prefix='source/tree/')


class ConversationReviewEvidenceTests(unittest.TestCase):
    """Use original child histories plus explicitly synthetic access-log fixtures, never native-route claims."""

    @classmethod
    def setUpClass(cls):
        """Inspect and retain the original session/data/tasks shapes directly from an immutable old capsule."""
        cls.files = {}
        with tarfile.open(ROOT/'docs/evidence/mentor-runtime-20261107/ci-receipts.tar.gz') as archive:
            prefix = 'release14/agent-request/'
            for name in ['coordinator-response.json', 'retrieval-session.json', 'retrieval-tasks.json',
                         'drift-session.json', 'drift-tasks.json']:
                cls.files['conversation/'+name] = archive.extractfile(prefix+name).read()
        parent = json.loads(cls.files['conversation/coordinator-response.json'])['result']['contextId']
        cls.child_paths = {role: {kind: 'conversation/'+role+'-'+kind+'.json' for kind in ['session', 'tasks']}
                           for role in ['retrieval', 'drift']}
        workers = {'10.42.0.17': {'role': 'coordinator', 'pod': 'fixture-coordinator-pod', 'uid': 'fixture-pod-uid'}}
        cls.files['conversation/workers.json'] = encoded(workers)
        cls.files['readbacks/worker-pods.json'] = encoded({'kind': 'PodList', 'items': [
            {'kind': 'Pod', 'apiVersion': 'v1',
             'metadata': {'name': 'fixture-coordinator-pod', 'uid': 'fixture-pod-uid', 'namespace': 'iot-agents',
                          'labels': {'ate.dev/worker-pool': 'iot-coordinator-pool'}},
             'status': {'phase': 'Running', 'podIP': '10.42.0.17'}}]})
        rows = []
        for role in ['retrieval', 'drift']:
            session = json.loads(cls.files['conversation/'+role+'-session.json'])['data']['session']['id']
            rows.append({'route': 'listener/delegate-'+role, 'http.method': 'POST',
                'http.path': '/api/a2a-sandboxes/iot-agents/iot-'+role, 'http.status': 200,
                'src.addr': '10.42.0.17:43001', 'a2a.method': 'message/send',
                'a2a.response.outcome': 'success', 'a2a.result.kind': 'task', 'a2a.task.state': 'completed',
                'a2a.context.id': session, 'a2a_parent_context_id': parent, 'a2a_root_context_id': parent, 'a2a_source': 'agent'})
        cls.files['conversation/gateway.jsonl'] = b''.join(encoded(row) for row in rows)

    def verify(self, files, **options):
        """Re-run content and transport acceptance against independent raw worker Pods."""
        return CHECK.verify_conversation(files, response_path='conversation/coordinator-response.json',
            child_paths=self.child_paths, gateway_path='conversation/gateway.jsonl',
            workers_path='conversation/workers.json', worker_pods_path='readbacks/worker-pods.json', **options)

    def test_original_child_histories_and_exact_context_transport_are_both_required(self):
        """A transport-only fixture cannot substitute for the original accepted MCP contents."""
        result = self.verify(self.files)
        self.assertTrue(result['coordinator']['coordinator_delegation_verified'])
        self.assertTrue(result['transport']['delegation_transport_verified'])
        self.assertEqual(result['transport']['successful_delegations'], 2)
        self.assertTrue(result['worker_inventory_verified'])

    def test_edited_child_content_source_transport_and_pod_uid_are_rejected(self):
        """Wrong answers, forged source markers and mismatched actual Pod readbacks each fail independently."""
        for mode in ['child', 'transport', 'worker', 'namespace', 'kind', 'api-version', 'ip-type']:
            files = dict(self.files)
            if mode == 'child':
                value = json.loads(files['conversation/retrieval-tasks.json'])
                value['data'][0]['artifacts'][0]['parts'][0]['text'] = '{}'
                files['conversation/retrieval-tasks.json'] = encoded(value)
            elif mode == 'transport':
                rows = [json.loads(line) for line in files['conversation/gateway.jsonl'].splitlines()]
                rows[0]['a2a_source'] = 'operator'
                files['conversation/gateway.jsonl'] = b''.join(encoded(row) for row in rows)
            else:
                value = json.loads(files['readbacks/worker-pods.json'])
                if mode == 'namespace':
                    value['items'][0]['metadata']['namespace'] = 'untrusted-namespace'
                elif mode == 'kind':
                    value['items'][0]['kind'] = 'Service'
                elif mode == 'api-version':
                    value['items'][0]['apiVersion'] = 'unreviewed/v1'
                elif mode == 'ip-type':
                    value['items'][0]['status']['podIP'] = int.from_bytes(bytes([10, 42, 0, 17]), 'big')
                else:
                    value['items'][0]['metadata']['uid'] = 'different-native-pod'
                files['readbacks/worker-pods.json'] = encoded(value)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.verify(files)

    def test_malformed_request_or_unreviewed_plain_text_cannot_hide_an_extra_call(self):
        """A complete pair does not excuse truncated JSON or silently dropped process output."""
        for extra in [b'{"route":"listener/delegate-retrieval","a2a.task.state":"failed"',
                      b'fixture startup text that was not explicitly reviewed']:
            files = dict(self.files)
            files['conversation/gateway.jsonl'] += extra + b'\n'
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.verify(files)

    def test_only_exact_explicit_non_request_startup_text_can_be_allowed(self):
        """An approved startup line remains counted; it cannot authorize a malformed JSON record."""
        files = dict(self.files)
        line = 'fixture startup; no request'
        files['conversation/gateway.jsonl'] += line.encode() + b'\n'
        result = self.verify(files, allowed_non_json_lines=[line])
        self.assertEqual(result['non_json_gateway_lines'], 1)
        files['conversation/gateway.jsonl'] += b'{"malformed":"JSON"\n'
        with self.assertRaises(ValueError):
            self.verify(files, allowed_non_json_lines=[line, '{"malformed":"JSON"'])


class NativeArchiveTests(unittest.TestCase):
    """Archive integrity is authenticated before any payload becomes verifier input."""

    def archive(self, payload_name='native/input.json'):
        """Create a tiny in-memory archive fixture with a pinned complete member inventory."""
        body = b'{"fixture":true}\n'
        manifest = encoded({'files': {payload_name: {'bytes': len(body), 'sha256': sha256(body).hexdigest()}}})
        stream = BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for name, raw in [('manifest.json', manifest), (payload_name, body)]:
                info = tarfile.TarInfo(name); info.size = len(raw)
                archive.addfile(info, BytesIO(raw))
        return stream.getvalue(), sha256(manifest).hexdigest()

    def test_exact_archive_and_manifest_pins_are_required(self):
        """Changed archives or escaped member names cannot pass even a matching member hash."""
        raw, manifest = self.archive()
        files = CHECK.load_native_archive(raw, expected_archive_sha256=sha256(raw).hexdigest(), expected_manifest_sha256=manifest)
        self.assertEqual(set(files), {'native/input.json'})
        with self.assertRaises(ValueError):
            CHECK.load_native_archive(raw, expected_archive_sha256='0'*64, expected_manifest_sha256=manifest)
        raw, manifest = self.archive('../outside.json')
        with self.assertRaises(ValueError):
            CHECK.load_native_archive(raw, expected_archive_sha256=sha256(raw).hexdigest(), expected_manifest_sha256=manifest)


if __name__ == '__main__':
    unittest.main()
