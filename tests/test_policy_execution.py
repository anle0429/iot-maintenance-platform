"""Verify a pinned OPA process boundary before any command can reach its runner."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import json
import os
import subprocess
import unittest

from agents.admission import evaluate
from agents.policy_execution import PolicyExecutionLedger


class PolicyExecutionTests(unittest.TestCase):
    """The ledger records dispatched OPA processes and rejects other work before execution."""

    def temporary(self):
        """Keep inert test files inside the writable checkout on restricted Windows hosts."""
        return TemporaryDirectory(prefix='policy-execution-test-',
            dir=Path(__file__).resolve().parents[1] if os.name == 'nt' else None)

    def binary(self, root):
        """Create inert bytes whose identity can be checked without launching an executable."""
        path = Path(root) / 'opa'
        path.write_bytes(b'pinned synthetic OPA bytes')
        return path.resolve(), sha256(path.read_bytes()).hexdigest()

    def test_unapproved_commands_are_rejected_before_the_runner(self):
        """A second executable, shell, alternate executable or unapproved verb cannot dispatch."""
        with self.temporary() as folder:
            opa, digest = self.binary(folder)
            other = Path(folder) / 'different-opa'
            other.write_bytes(opa.read_bytes())
            runner = Mock()
            ledger = PolicyExecutionLedger(opa, digest, runner=runner)
            for argv, options in [
                ([str(other), 'eval', 'true'], {}),
                ([str(opa), 'run'], {}),
                ([str(opa), 'exec'], {}),
                ([str(opa), 'eval', 'true'], {'shell': True}),
                ([str(opa), 'eval', 'true'], {'executable': str(other)}),
                (str(opa) + ' eval true', {}),
            ]:
                with self.subTest(argv=argv, options=options), self.assertRaises(ValueError):
                    ledger(argv, **options)
            runner.assert_not_called()
            self.assertEqual(ledger.summary()['invocation_count'], 0)

    def test_changed_binary_is_rejected_before_the_runner(self):
        """Checking the pin at construction alone cannot authorize later replacement bytes."""
        with self.temporary() as folder:
            opa, digest = self.binary(folder)
            runner = Mock()
            ledger = PolicyExecutionLedger(opa, digest, runner=runner)
            opa.write_bytes(b'changed synthetic OPA bytes')
            with self.assertRaises(ValueError):
                ledger([str(opa), 'version'])
            runner.assert_not_called()
            self.assertEqual(ledger.summary()['invocation_count'], 0)

    def test_completed_invocations_supply_the_summary_and_output_hashes(self):
        """Returned results, including a fixture denial exit, determine the detached ledger."""
        with self.temporary() as folder:
            opa, digest = self.binary(folder)
            commands = [[str(opa), 'version'], [str(opa), 'check', '--strict', 'policy.rego'],
                        [str(opa), 'eval', '--fail', 'false = true']]
            results = [subprocess.CompletedProcess(commands[0], 0, b'Version: fixture\n', b''),
                       subprocess.CompletedProcess(commands[1], 0, b'', b''),
                       subprocess.CompletedProcess(commands[2], 1, b'{}\n', b'fixture stderr\n')]
            runner = Mock(side_effect=results)
            ledger = PolicyExecutionLedger(opa, digest, runner=runner)
            for argv, result in zip(commands, results, strict=True):
                self.assertIs(ledger(argv, capture_output=True, timeout=30), result)
            summary = ledger.summary()
            self.assertEqual(summary['invocation_count'], 3)
            self.assertEqual(summary['verb_counts'], {'version': 1, 'check': 1, 'eval': 1})
            for row, argv, result in zip(summary['invocations'], commands, results, strict=True):
                self.assertEqual(row['argv'], argv)
                self.assertEqual(row['verb'], argv[1])
                self.assertEqual(row['exit_code'], result.returncode)
                self.assertEqual(row['stdout_sha256'], sha256(result.stdout).hexdigest())
                self.assertEqual(row['stderr_sha256'], sha256(result.stderr).hexdigest())
            summary['invocations'][0]['argv'][0] = 'changed copy'
            self.assertEqual(ledger.summary()['invocations'][0]['argv'][0], str(opa))
            self.assertNotIn('model_requests', summary)
            self.assertNotIn('network_requests', summary)
            self.assertNotIn('deployment_commands_run', summary)

    def test_default_runner_uses_captured_subprocess_output(self):
        """The normal ledger dispatches its approved command through subprocess.run."""
        with self.temporary() as folder:
            opa, digest = self.binary(folder)
            result = subprocess.CompletedProcess([str(opa), 'version'], 0, b'fixture\n', b'')
            with patch('agents.policy_execution.subprocess.run', return_value=result) as runner:
                ledger = PolicyExecutionLedger(opa, digest)
                self.assertIs(ledger([str(opa), 'version']), result)
            runner.assert_called_once_with([str(opa), 'version'], capture_output=True)
            self.assertEqual(ledger.summary()['invocation_count'], 1)

    def source(self, root, opa, digest):
        """Supply isolated source pins for the actual evaluate wrapper's command construction."""
        source = Path(root) / 'source'
        for name, body in {
            'infra/policy/opa-assets.json': json.dumps({'bytes': opa.stat().st_size,
                'sha256': digest, 'version': 'fixture'}),
            'infra/policy/agent-admission.rego': 'package fixture\n',
            'infra/policy/agent-admission-approved.json': '{}\n',
            'agents/response-policy.json': '{}\n',
        }.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding='utf-8')
        return source

    def test_evaluate_records_both_command_results_through_optional_runner(self):
        """Injecting the ledger preserves decision parsing, raw outputs and the denial exit."""
        with self.temporary() as folder:
            opa, digest = self.binary(folder)
            source = self.source(folder, opa, digest)
            decision = {'result': [{'expressions': [{'value': {
                'allowed': False, 'violations': ['fixture denied object']}}]}]}
            runner = Mock(side_effect=[subprocess.CompletedProcess([], 0, json.dumps(decision).encode(), b''),
                                       subprocess.CompletedProcess([], 1, b'{}', b'')])
            ledger = PolicyExecutionLedger(opa, digest, runner=runner)
            output = Path(folder) / 'output'
            report = evaluate(source, opa, {'resources': []}, output, command_runner=ledger)
            self.assertFalse(report['allowed'])
            self.assertEqual(report['gate_exit'], 1)
            self.assertEqual(report['opa_sha256'], digest)
            self.assertEqual((output / 'gate.stdout').read_bytes(), b'{}')
            self.assertEqual(ledger.summary()['verb_counts'], {'eval': 2})
            self.assertTrue(all(row['argv'][0] == str(opa) for row in ledger.summary()['invocations']))

    def test_evaluate_still_checks_asset_pin_before_dispatch(self):
        """The optional runner cannot bypass the existing evaluate binary asset check."""
        with self.temporary() as folder:
            opa, digest = self.binary(folder)
            source = self.source(folder, opa, digest)
            runner = Mock()
            opa.write_bytes(b'replaced before evaluate')
            with self.assertRaises(ValueError):
                evaluate(source, opa, {'resources': []}, Path(folder) / 'output', command_runner=runner)
            runner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
