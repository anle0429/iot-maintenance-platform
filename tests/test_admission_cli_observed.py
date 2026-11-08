"""Test evaluator input selection and process accounting with explicitly synthetic OPA replies."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('observed_admission_cli', ROOT/'scripts/check-agent-admission.py')
CLI = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(CLI)


class ObservedAdmissionCliTests(unittest.TestCase):
    """No fixture response in this test is model, Rego or cluster execution evidence."""

    def test_selected_observed_input_and_actual_command_ledger_are_used(self):
        """A current envelope must replace the old fixture; denied receipts cannot authorize deployment."""
        names = ['agents/admission.py', 'agents/admission_cases.py', 'agents/policy_execution.py',
            'scripts/check-agent-admission.py', 'infra/policy/agent-admission.rego',
            'infra/policy/agent-admission-approved.json', 'agents/response-policy.json']
        with TemporaryDirectory(dir=ROOT/'outputs' if os.name == 'nt' else None) as directory:
            root = Path(directory)
            for name in names:
                target = root/name; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT/name).read_bytes())
            binary = root/'opa-fixture'; binary.write_bytes(b'not an executable; subprocess is mocked')
            assets = {'bytes': binary.stat().st_size, 'sha256': sha256(binary.read_bytes()).hexdigest(), 'version': 'fixture-only'}
            (root/'infra/policy/opa-assets.json').write_text(json.dumps(assets))
            value = {'selected_observed': True, 'resources': [{'kind': 'SandboxAgent',
                'metadata': {'name': 'iot-retrieval'}, 'spec': {'declarative': {'tools': [
                    {'mcpServer': {'toolNames': ['get_machine_features']}}]}}}]}
            observed = root/'observed.json'; observed.write_text(json.dumps(value))
            old = root/'tests/fixtures/agent-admission-observed.json'; old.parent.mkdir(parents=True)
            old.write_text(json.dumps({**value, 'selected_observed': False}))
            seal = root/'source.json'; seal.write_text(json.dumps({'format': 'iot-agent-admission-source-v1',
                'commit': 'f'*40, 'files': {name: sha256((root/name).read_bytes()).hexdigest() for name in names}}))
            (root/'outputs').mkdir()

            def declared(baseline):
                """Declare two driver fixtures without copying the real policy rules."""
                invalid = deepcopy(baseline)
                invalid['resources'][0]['spec']['declarative']['tools'][0]['mcpServer']['toolNames'].append('start_pump')
                return [{'id': 'baseline', 'input': baseline, 'expected_allowed': True},
                        {'id': 'unsafe', 'input': invalid, 'expected_allowed': False}]

            def dispatch(argv, **kwargs):
                """Emit synthetic OPA JSON; never launch the placeholder executable."""
                if argv[1] == 'check': return subprocess.CompletedProcess(argv, 0, b'', b'')
                if argv[1] == 'version': return subprocess.CompletedProcess(argv, 0, b'fixture only', b'')
                supplied = json.loads(Path(argv[argv.index('--input')+1]).read_bytes())
                tools = supplied['resources'][0]['spec']['declarative']['tools'][0]['mcpServer']['toolNames']
                allowed = supplied['selected_observed'] and 'start_pump' not in tools
                if argv[-1].endswith(' = true'):
                    return subprocess.CompletedProcess(argv, 0 if allowed else 1, b'{}', b'')
                decision = {'allowed': allowed, 'violations': [] if allowed else ['fixture denial']}
                body = json.dumps({'result': [{'expressions': [{'value': decision}]}]}).encode()
                return subprocess.CompletedProcess(argv, 0, body, b'')

            args = SimpleNamespace(source_manifest=seal, opa=binary, output=root/'outputs/result', observed=observed)
            with patch.object(CLI, 'ROOT', root), patch.object(CLI, 'cases', declared), \
                    patch('subprocess.run', side_effect=dispatch), patch('subprocess.check_output', return_value=b'fixture only'):
                CLI.run(args)
            report = json.loads((args.output/'verification.json').read_bytes())
            self.assertEqual(report['allowed_cases'], 1)
            self.assertEqual(report['denied_cases'], 1)
            self.assertIsNotNone(report.get('process_ledger'))
            self.assertEqual(report['process_ledger']['invocation_count'], 6)
            self.assertEqual(report['process_ledger']['verb_counts'], {'check': 1, 'version': 1, 'eval': 4})
            self.assertNotIn('model_requests', report)
            self.assertNotIn('deployment_commands_run', report)


if __name__ == '__main__': unittest.main()
