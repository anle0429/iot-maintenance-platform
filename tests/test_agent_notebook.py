"""Reject notebook source tampering and escaped files before native requests, without simulating live answers."""
from hashlib import sha256
from pathlib import Path
import ast
import importlib.util
import json
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('agent_notebook', ROOT / 'scripts/run-agent-notebook.py')
runner = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(runner)


class AgentNotebookTests(unittest.TestCase):
    """Use tiny independent seals; the real Jupyter run establishes deployment and factual correctness."""

    def test_source_seal_rejects_changed_request_code(self):
        """A modified client cannot inherit acceptance from a previously sealed notebook bundle."""
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'tree'; files = {}
            for name in [runner.NOTEBOOK, 'scripts/run-agent-notebook.py', 'agents/__init__.py',
                         'agents/probe.py', 'agents/answer_acceptance.py', 'knowledge/catalog.json']:
                path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'original')
                files[name] = sha256(b'original').hexdigest()
            (source.parent / 'manifest.json').write_text(json.dumps({'files': files}))
            self.assertEqual(runner.source_manifest(source)['files'], files)
            (source / 'agents/probe.py').write_bytes(b'modified')
            with self.assertRaises(ValueError): runner.source_manifest(source)

    def test_incomplete_or_escaped_source_cannot_be_accepted(self):
        """A missing catalog or correctly hashed file outside the bundle must fail."""
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'tree'; files = {}
            for name in [runner.NOTEBOOK, 'scripts/run-agent-notebook.py', 'agents/__init__.py',
                         'agents/probe.py', 'agents/answer_acceptance.py', 'knowledge/catalog.json']:
                path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'x')
                files[name] = sha256(b'x').hexdigest()
            manifest = source.parent / 'manifest.json'
            for case in ['missing', 'escaped']:
                altered = dict(files)
                if case == 'missing': del altered['knowledge/catalog.json']
                else:
                    (source.parent / 'outside').write_bytes(b'x'); altered['../outside'] = sha256(b'x').hexdigest()
                manifest.write_text(json.dumps({'files': altered}))
                with self.subTest(case=case), self.assertRaises(ValueError): runner.source_manifest(source)

    def test_source_notebook_is_executable_python_without_invented_outputs(self):
        """Keep a clean source notebook and a valid JSON-receipt newline, leaving live proof to native execution."""
        document = json.loads((ROOT / runner.NOTEBOOK).read_bytes())
        code = [cell for cell in document['cells'] if cell['cell_type'] == 'code']
        self.assertEqual(len(code), 7)
        for cell in code:
            self.assertIsNone(cell['execution_count']); self.assertEqual(cell['outputs'], [])
            compile(''.join(cell['source']), runner.NOTEBOOK, 'exec')
        constants = [n.value for n in ast.walk(ast.parse(''.join(code[-1]['source']))) if isinstance(n, ast.Constant)]
        self.assertIn('\n', constants)
        self.assertNotIn('\\n', constants)


if __name__ == '__main__': unittest.main()
