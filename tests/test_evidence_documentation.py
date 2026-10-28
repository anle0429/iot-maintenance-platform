"""Protect historical byte bindings while permitting current documentation without accepting executable changes."""
import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('evidence_integrity_test',Path(__file__).resolve().parents[1]/'scripts/evidence_integrity.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class DocumentationEquivalenceTests(unittest.TestCase):
    """Use paired source snippets to distinguish documentation from executable or configuration changes."""
    def test_nested_documentation_is_allowed(self):
        """Permit module, class and async-function docstrings while retaining decorators/defaults and body."""
        original=b'class Guard:\n @staticmethod\n async def accepted(value=True):\n  return value\n'
        changed=b'"Purpose"\nclass Guard:\n "Boundary"\n @staticmethod\n async def accepted(value=True):\n  "Decision"\n  return value\n'
        self.assertTrue(module.documentation_equivalent(original,changed,'.py'))

    def test_logic_defaults_decorators_and_nonleading_strings_are_rejected(self):
        """Reject four changes that could alter a gate even when accompanied by harmless documentation."""
        original=b'@trusted\ndef allow(value=False):\n return value\n'
        for changed in [b'@trusted\ndef allow(value=False):\n "Doc"\n return True\n',b'@trusted\ndef allow(value=True):\n return value\n',b'@other\ndef allow(value=False):\n return value\n',b'@trusted\ndef allow(value=False):\n pass\n "Not a docstring"\n return value\n']:
            with self.subTest(source=changed):self.assertFalse(module.documentation_equivalent(original,changed,'.py'))

    def test_configuration_and_invalid_python_cannot_use_the_exception(self):
        """Require byte identity for non-Python assets and reject unparsable Python changes."""
        self.assertFalse(module.documentation_equivalent(b'{"allowed":false}',b'{"allowed":true}','.json'))
        self.assertFalse(module.documentation_equivalent(b'def ok():return True',b'def broken(','.py'))
        self.assertTrue(module.documentation_equivalent(b'unchanged',b'unchanged','.json'))
