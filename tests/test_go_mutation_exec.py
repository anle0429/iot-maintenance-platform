"""Check that the strict Go mutation harness preserves source and never counts compilation errors as killed tests."""
from hashlib import sha256
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch
import importlib.util
import json
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('go_mutation_exec', ROOT / 'scripts/go-mutation-exec.py')
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)


class StrictGoMutationTests(unittest.TestCase):
    """Exercise real disposable files with mocked Go test output, separately from native mutation evidence."""

    def invoke(self, result):
        """Apply one mutation, retain its classification and confirm unconditional source restoration."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); work = base / 'work'; work.mkdir(); original = work / 'main.go'
            original.write_bytes(b'original source'); changed = base / 'mutant'; changed.write_bytes(b'mutated source')
            output = base / 'results'
            env = {'IOT_MUTATION_WORKDIR': str(work), 'IOT_MUTATION_RESULTS': str(output),
                'MUTATE_ORIGINAL': str(original), 'MUTATE_CHANGED': str(changed),
                'IOT_MUTATION_SOURCE_SHA256': sha256(b'original source').hexdigest(),
                'IOT_GO': 'test-only-go', 'IOT_MUTATION_MIN_TESTS': '1'}
            with patch.dict('os.environ', env), patch.object(runner.subprocess, 'run') as call:
                if isinstance(result, Exception): call.side_effect = result
                else: call.return_value = result
                code = runner.main()
            self.assertEqual(original.read_bytes(), b'original source')
            record = json.loads((output / 'records.jsonl').read_bytes())
            self.assertEqual(record['mutant_sha256'], sha256(b'mutated source').hexdigest())
            return code, record

    def test_a_real_named_test_failure_is_a_kill(self):
        """Count a nonzero Go process with an attributable failed test as an actual test kill."""
        events = b'{"Action":"run","Test":"TestBoundary"}\n{"Action":"fail","Test":"TestBoundary"}\n'
        code, row = self.invoke(CompletedProcess([], 1, events, b''))
        self.assertEqual(code, 0); self.assertEqual(row['reason'], 'killed_by_failing_test')

    def test_successful_test_execution_is_a_survivor(self):
        """An executed passing suite leaves the mutation alive regardless of its source change."""
        code, row = self.invoke(CompletedProcess([], 0, b'{"Action":"run","Test":"TestBoundary"}\n', b''))
        self.assertEqual(code, 1); self.assertEqual(row['reason'], 'survived')

    def test_build_failure_and_empty_suite_are_never_kills(self):
        """Compilation errors and an empty successful process cannot increase the test-kill numerator."""
        for result in [CompletedProcess([], 1, b'{"Action":"fail"}\n', b'build failed'), CompletedProcess([], 0, b'', b'')]:
            with self.subTest(result=result):
                code, row = self.invoke(result)
                self.assertEqual(code, 2); self.assertEqual(row['reason'], 'compilation_or_test_harness_failure')

    def test_timeout_restores_source_without_claiming_a_test_kill(self):
        """Retain partial timeout output and restore original bytes without treating a timeout as a test failure."""
        code, row = self.invoke(TimeoutExpired('test-only-go', 8, output=b'partial output'))
        self.assertEqual(code, 2); self.assertEqual(row['reason'], 'test_timeout')


if __name__ == '__main__':
    unittest.main()
