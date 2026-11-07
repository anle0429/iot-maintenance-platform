"""Reject misleading outcomes and incomplete original graphs in the mentor follow-up verifier."""
from pathlib import Path
import copy
import importlib.util
import json
import sys
import tarfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('mentor_runtime_check', ROOT / 'scripts/verify-mentor-runtime.py')
CHECK = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(CHECK)


class MentorRuntimeEvidenceTests(unittest.TestCase):
    """Use actual captured span IDs and terminal task bodies for positive and negative graph checks."""

    @classmethod
    def setUpClass(cls):
        """Read the original completed graph once without running any native service."""
        with tarfile.open(ROOT / 'docs/evidence/mentor-runtime-20261107/native-receipts.tar.gz') as archive:
            prefix = 'native/a2a-outcomes-joined-20261106/completed-'
            cls.raw = archive.extractfile(prefix + 'jaeger.jsonl').read()
            cls.client = json.load(archive.extractfile(prefix + 'client-trace.json'))
            cls.response = json.load(archive.extractfile(prefix + 'response.json'))

    def test_original_graph_is_complete_and_task_grounded(self):
        """The accepted original root is OK, has the actual task ID and closes all 44 spans."""
        report = CHECK.trace_graph(self.raw, self.client, self.response)
        self.assertEqual(report['spans'], 44); self.assertEqual(report['status_code'], 1)
        self.assertEqual(report['missing_parents'], []); self.assertEqual(len(report['services']), 7)

    def test_missing_parent_unknown_service_and_false_outcome_are_rejected(self):
        """A pretty screenshot cannot substitute for parent closure, emitter identity or task success."""
        lines = [json.loads(line) for line in self.raw.splitlines()]
        for case in ['parent', 'service', 'status', 'task']:
            changed = copy.deepcopy(lines); client = copy.deepcopy(self.client)
            resource = changed[0]['result']['resourceSpans'][0]
            span = resource['scopeSpans'][0]['spans'][0]
            if case == 'parent': span['parentSpanId'] = 'f' * 16
            elif case == 'service':
                next(a for a in resource['resource']['attributes'] if a['key'] == 'service.name')['value']['stringValue'] = 'missing-service-name'
            elif case == 'status': client['status_code'] = 2
            else: client['task_id'] = 'b' * 32
            raw = b'\n'.join(json.dumps(line).encode() for line in changed)
            with self.subTest(case=case), self.assertRaises(ValueError): CHECK.trace_graph(raw, client, self.response)
