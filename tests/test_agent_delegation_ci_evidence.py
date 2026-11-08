"""Check CI proof primitives against original captures and explicitly synthetic decision-driver fixtures."""
from copy import deepcopy
from pathlib import Path
import json
import re
import tarfile
import unittest
from unittest.mock import patch

from tests import test_agent_delegation_review_evidence as review


ROOT = Path(__file__).resolve().parents[1]
CHECK = review.CHECK


class CiObservationEvidenceTests(unittest.TestCase):
    """Independent counters and complete native resource identities cannot be replaced by summary claims."""

    @classmethod
    def setUpClass(cls):
        """Read the immutable original negative CI capture without extracting or modifying evidence."""
        cls.files = {}
        with tarfile.open(ROOT/'docs/evidence/agent-admission/native-receipts.tar.gz') as archive:
            prefix = 'agent-admission-capture-30a/'
            for entry in archive:
                if entry.isfile():
                    cls.files[entry.name[len(prefix):]] = archive.extractfile(entry).read()

    def test_real_gpu_counter_equality_and_invalid_counters(self):
        """The old denied build has equal observed counters; missing/duplicate/non-finite values fail."""
        before = self.files['native/agent-policy-ci-trigger-negative-30a/model-before.prom']
        after = self.files['native/agent-policy-ci-finish-negative-30a/model-after.prom']
        self.assertEqual(CHECK.gpu_output_tokens(before), CHECK.gpu_output_tokens(after))
        for raw in [b'', b'llamacpp:tokens_predicted_total NaN\n', b'llamacpp:tokens_predicted_total -1\n',
                    b'llamacpp:tokens_predicted_total 1\nllamacpp:tokens_predicted_total 1\n']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                CHECK.gpu_output_tokens(raw)

    def test_actual_five_kind_uids_and_full_specs_are_compared(self):
        """Volatile status is excluded, but duplicate objects and changed UID/spec cannot disappear."""
        before = json.loads(self.files['native/agent-policy-ci-trigger-negative-30a/resources-before.json'])
        after = json.loads(self.files['native/agent-policy-ci-finish-negative-30a/resources-after.json'])
        self.assertEqual(CHECK.resource_identities(before), CHECK.resource_identities(after))
        self.assertEqual(sum(len(items) for items in CHECK.resource_identities(before).values()), 22)
        for field, value in [('uid', 'different-pod-or-resource'), ('spec', {'changed': True})]:
            changed = deepcopy(after)
            target = changed['sandboxagents']['items'][0]
            if field == 'uid': target['metadata']['uid'] = value
            else: target['spec'] = value
            self.assertNotEqual(CHECK.resource_identities(before), CHECK.resource_identities(changed))
        duplicate = deepcopy(before)
        duplicate['sandboxagents']['items'].append(deepcopy(duplicate['sandboxagents']['items'][0]))
        with self.assertRaises(ValueError): CHECK.resource_identities(duplicate)

    def test_jenkins_artifact_index_requires_the_complete_original_directory(self):
        """A missing deployment artifact cannot be manufactured by selectively copying the output directory."""
        build = json.loads(self.files['native/agent-policy-ci-finish-negative-30a/build.json'])
        prefix = 'ci/llm-coordinator-jenkins-4/'
        selected = {name: body for name, body in self.files.items() if name.startswith(prefix)}
        names = CHECK.ci_artifact_files(selected, prefix, build)
        self.assertIn('policy-decision/input.json', names)
        self.assertNotIn('deploy.json', names)
        selected.pop(prefix+'policy-decision/input.json')
        with self.assertRaises(ValueError): CHECK.ci_artifact_files(selected, prefix, build)


class CiDecisionEvidenceTests(unittest.TestCase):
    """Decision-driver fixtures are not claimed as actual native OPA or CI execution."""

    @classmethod
    def setUpClass(cls):
        """Reuse declared current case inputs without copying or simulating Rego policy rules."""
        cls.files, _ = review.policy_fixture()
        cls.assets = json.loads(cls.files['source/tree/infra/policy/opa-assets.json'])
        seal = json.loads(cls.files['policy/source.json'])
        cls.pins = {name: seal['files'][name] for name in CHECK.POLICIES}

    def test_only_the_original_explicit_false_and_exit_one_establish_denial(self):
        """An actual denial requires matching raw decision, gate and configuration/hash receipt."""
        prefix = 'policy/unsafe-hardware-tool/'
        receipt, value = CHECK.verify_ci_decision(self.files, prefix, self.pins, self.assets, allowed=False)
        self.assertFalse(receipt['allowed'])
        self.assertEqual(receipt['gate_exit'], 1)
        self.assertEqual(value, json.loads(self.files[prefix+'input.json']))
        for mode in ['gate', 'decision', 'hash']:
            files = dict(self.files)
            if mode == 'gate':
                files[prefix+'gate-exit.json'] = review.encoded({'exit_code': 0})
            elif mode == 'decision':
                files[prefix+'decision.stdout'] = review.encoded({'result': [{'expressions': [
                    {'value': {'allowed': True, 'violations': []}}]}]})
            else:
                report = json.loads(files[prefix+'verification.json']); report['input_sha256'] = '0'*64
                files[prefix+'verification.json'] = review.encoded(report)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                CHECK.verify_ci_decision(files, prefix, self.pins, self.assets, allowed=False)


class ActualDelegationCiEvidenceTests(unittest.TestCase):
    """Recheck the actual new public CI18/19 pair and mutate only in-memory copies of its original bytes."""

    @classmethod
    def setUpClass(cls):
        """Authenticate the unchanged public native archive before selecting actual CI payloads."""
        base = ROOT/'docs/evidence/agent-delegation-20261108'
        capture = json.loads((base/'native-archive.json').read_bytes())
        cls.files = CHECK.load_native_archive((base/'native-receipts.tar.gz').read_bytes(),
            expected_archive_sha256=capture['archive_sha256'], expected_manifest_sha256=capture['manifest_sha256'])

    def verify(self, files):
        """Require the genuine isolated denial window and the independently accepted production run."""
        return CHECK.verify_ci(files, 'ci/denied-c/', 'ci/allowed-b/', 'ci/build-19/', 'ci/build-18/',
                               'source-ci/', expected_source_files=402)

    def test_actual_ci19_denial_and_ci18_release_are_independently_accepted(self):
        """Actual snapshots, process results, v1 prompts and real child histories all agree."""
        result = self.verify(self.files)
        self.assertEqual(result['denied_build'], 19)
        self.assertEqual(result['allowed_build'], 18)
        self.assertEqual(sum(result['unchanged_denied_resources'].values()), 27)
        self.assertEqual(result['denied_gpu_output_token_delta'], 0.0)
        self.assertEqual(result['allowed_gpu_output_token_delta'], 1139.0)
        self.assertTrue(result['accepted_coordinator']['coordinator_delegation_verified'])

    def test_review_index_checks_initial_and_post_ci_conversations_separately(self):
        """The actual UI-owned pair and the actual post-Helm pair require their own child histories and wire logs."""
        def conversation(prefix, *, initial=False):
            """Select verbatim filenames from each of the two independently captured native formats."""
            return {'response_path': prefix+'coordinator-response.json',
                'child_paths': {role: {kind: prefix+role+'-'+kind+('-response' if initial else '')+'.json'
                               for kind in ['session', 'tasks']} for role in ['retrieval', 'drift']},
                'gateway_path': prefix+'gateway.log', 'workers_path': prefix+'workers.json',
                'worker_pods_path': prefix+'worker-pods.json'}
        observed = json.loads(self.files['policy/observed.json'])
        index = {'format': 'iot-agent-delegation-review-v1',
            'policy': {'policy_prefix': 'policy/', 'source_prefix': 'source/tree/',
                       'expected_case_ids': [case['id'] for case in CHECK.cases(observed)]},
            'conversation': conversation('ui-chat-b/', initial=True),
            'ci': {'denied_prefix': 'ci/denied-c/', 'allowed_prefix': 'ci/allowed-b/',
                   'denied_artifacts_prefix': 'ci/build-19/', 'allowed_artifacts_prefix': 'ci/build-18/',
                   'source_prefix': 'source-ci/', 'expected_source_files': 402},
            'post_ci_conversation': conversation('ci-conversation/')}
        result = CHECK.verify_review(self.files, index)
        before = result['conversation']['coordinator']['context_id']
        after = result['post_ci_conversation']['coordinator']['context_id']
        self.assertNotEqual(before, after)
        self.assertEqual(after, '3aa041a2-dda4-487f-9fad-f51e1ec2aa9c')
        self.assertEqual(after, result['ci']['accepted_coordinator']['context_id'])
        self.assertEqual(result['post_ci_conversation']['transport']['successful_delegations'], 2)

    def test_mutated_actual_ci_proof_cannot_hide_work_or_borrow_an_approval(self):
        """Each original observation, artifact boundary and release/configuration identity remains necessary."""
        for mode in ['gpu', 'resource', 'helm', 'gate', 'forbidden', 'phase', 'prompt', 'recheck', 'child', 'missing-artifact', 'skip-console']:
            files = dict(self.files)
            if mode == 'gpu':
                name = 'ci/denied-c/model-after.prom'
                files[name], count = re.subn(rb'(?m)^(llamacpp:tokens_predicted_total )([^\r\n]+)',
                    lambda match: match[1]+str(float(match[2])+1).encode(), files[name])
                self.assertEqual(count, 1)
            elif mode == 'resource':
                name = 'ci/denied-c/resources-after.json'; value = json.loads(files[name])
                value['deployments']['items'][0]['metadata']['uid'] = 'replacement-resource-uid'
                files[name] = review.encoded(value)
            elif mode == 'helm':
                name = 'ci/denied-c/helm-after.json'; value = json.loads(files[name])
                value['iot-agents'][0]['revision'] += 1; files[name] = review.encoded(value)
            elif mode == 'gate':
                files['ci/build-19/policy-decision/gate-exit.json'] = review.encoded({'exit_code': 0})
            elif mode == 'forbidden':
                files['ci/build-19/deploy.json'] = b'{}\n'
                name = 'ci/denied-c/build.json'; value = json.loads(files[name])
                value['artifacts'].append({'relativePath': 'build-19/outputs/ci-agents/deploy.json'})
                files[name] = review.encoded(value)
            elif mode == 'phase':
                name = 'ci/build-18/test.json'; value = json.loads(files[name])
                value['source_commit'] = '0'*40; files[name] = review.encoded(value)
            elif mode == 'prompt':
                name = 'ci/build-18/prompts.json'; value = json.loads(files[name])
                value['prompts']['retrieval']['version'] = 2; files[name] = review.encoded(value)
            elif mode == 'recheck':
                name = 'ci/build-18/policy-recheck-decision/input.json'; value = json.loads(files[name])
                next(row for row in value['resources'] if row['kind'] == 'ModelConfig')['spec']['openAI']['maxTokens'] = 767
                files[name] = CHECK.canonical(value)
                receipt_name = 'ci/build-18/policy-recheck-decision/verification.json'
                receipt = json.loads(files[receipt_name]); receipt['input_sha256'] = CHECK.sha256(files[name]).hexdigest()
                files[receipt_name] = review.encoded(receipt)
            elif mode == 'child':
                name = 'ci/build-18/agent-request/retrieval-tasks.json'; value = json.loads(files[name])
                context = json.loads(files['ci/build-18/agent-request/retrieval-session.json'])['data']['session']['id']
                next(row for row in value['data'] if row['contextId'] == context)['artifacts'][0]['parts'][0]['text'] = '{}'
                files[name] = review.encoded(value)
            elif mode == 'missing-artifact':
                files.pop('ci/build-18/policy-recheck-decision/input.json')
            else:
                name = 'ci/denied-c/console.txt'
                target = b'Stage "Auto deploy MCP and accepted agent policy" skipped due to earlier failure(s)'
                self.assertIn(target, files[name]); files[name] = files[name].replace(target, b'unattributed skipped stage')
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.verify(files)


class ActualDelegationSourcePacketTests(unittest.TestCase):
    """Bind the actual captured packets and endpoints to immutable sources without native execution."""

    @classmethod
    def setUpClass(cls):
        """Load authenticated original bytes once; mutations use independent in-memory mappings."""
        ActualDelegationCiEvidenceTests.setUpClass()
        cls.files = ActualDelegationCiEvidenceTests.files

    def test_both_actual_packets_and_final_gateway_match_immutable_sources(self):
        """Each packet contains exactly 144 commit blobs and 258 original archived inputs."""
        result = CHECK.verify_source_packets(self.files)
        self.assertTrue(result['passed'])
        self.assertEqual(set(result['source_packets']), {'source', 'source-ci'})
        for packet in result['source_packets'].values():
            self.assertEqual(packet['source_files'], 402)
            self.assertEqual(packet['tracked_git_files'], 144)
            self.assertEqual(packet['historical_evidence_files'], 258)
        self.assertEqual(result['listener_ports'], [18080, 18081, 18082, 18083])

    def test_rehashed_source_edits_cannot_borrow_a_commit_or_original_archive_pin(self):
        """Even a self-consistent replacement seal cannot authorize altered Git or historical bytes."""
        historical = json.loads((ROOT/'docs/evidence-archive/manifest.json').read_bytes())['files']
        for prefix in ['source/', 'source-ci/']:
            for mode in ['commit', 'git', 'historical', 'missing', 'extra', 'count']:
                files = dict(self.files)
                name = prefix+'manifest.json'; seal = json.loads(files[name])
                target = ('agents/admission.py' if mode != 'historical' else
                          next(n for n in seal['files'] if n in historical))
                if mode == 'commit':
                    seal['commit'] = '0'*40
                elif mode in {'git', 'historical'}:
                    files[prefix+'tree/'+target] += b'\nmodified source\n'
                    seal['files'][target] = CHECK.sha256(files[prefix+'tree/'+target]).hexdigest()
                elif mode == 'missing':
                    files.pop(prefix+'tree/'+target)
                elif mode == 'extra':
                    files[prefix+'tree/unrecorded.py'] = b'unrecorded\n'
                else:
                    seal['files'].pop(target); files.pop(prefix+'tree/'+target)
                files[name] = review.encoded(seal)
                with self.subTest(prefix=prefix, mode=mode), self.assertRaises(ValueError):
                    CHECK.verify_source_packets(files)

    def test_original_evidence_inventory_is_authenticated_before_using_its_pins(self):
        """A modified historical index cannot bless rehashed replacement source inputs."""
        path = ROOT/'docs/evidence-archive/manifest.json'
        original_read = Path.read_bytes
        def replaced_read(value):
            """Change only the local inventory read while preserving every original evidence file."""
            body = original_read(value)
            return body+b'\n' if value == path else body
        with patch.object(Path, 'read_bytes', replaced_read), self.assertRaisesRegex(ValueError, 'inventory'):
            CHECK.verify_source_packets(self.files)

    def test_gateway_and_both_compiler_flags_cannot_drift_from_captured_contract(self):
        """Fixed backends, delegated header routing, tracing and both advertised/discovery URLs are required."""
        for mode in ['backend', 'host-header', 'credential', 'tracing', 'listener', 'operator', 'a2a-url', 'proxy-url']:
            files = dict(self.files)
            if mode.endswith('-url'):
                name = 'native/kagent-values.json'; values = json.loads(files[name])
                if mode == 'a2a-url': values['controller']['a2aBaseUrl'] = 'http://kagent-controller.iot-agents:8083'
                else: values['proxy']['url'] = 'http://kagent-controller.iot-agents:8083'
                files[name] = review.encoded(values)
            else:
                name = 'final/gateway-configuration.json'; configmap = json.loads(files[name])
                value = json.loads(configmap['data']['config.yaml'])
                routes = value['binds'][3]['listeners'][0]['routes']
                delegate = next(row for row in routes if row['name'] == 'delegate-retrieval')
                if mode == 'backend': delegate['backends'][0]['host'] = 'other-controller.iot-agents:8083'
                elif mode == 'host-header': delegate['matches'][0].pop('headers')
                elif mode == 'credential':
                    operator = next(row for row in routes if row['name'] == 'a2a-coordinator')
                    delegate['policies']['apiKey'] = deepcopy(operator['policies']['apiKey'])
                elif mode == 'tracing': value['frontendPolicies'].pop('tracing')
                elif mode == 'listener': value['binds'].pop(1)
                else:
                    operator = next(row for row in routes if row['name'] == 'a2a-coordinator')
                    operator['policies']['requestHeaderModifier']['set']['x-user-id'] = 'different-user'
                configmap['data']['config.yaml'] = json.dumps(value)
                files[name] = review.encoded(configmap)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                CHECK.verify_source_packets(files)


if __name__ == '__main__':
    unittest.main()
