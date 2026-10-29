"""Keep a prompt-version change from silently widening any sandbox tool or network capability."""
from copy import deepcopy
from pathlib import Path
import importlib.util
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('agent_response_renderer', ROOT / 'scripts/render-agent-responses.py')
RENDERER = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(RENDERER)


class ResponseRendererTests(unittest.TestCase):
    """Compare the complete historical native objects before and after the narrowly scoped policy render."""

    def setUp(self):
        """Load recorded resource objects and the explicitly pending response policy without contacting Kubernetes."""
        self.original = json.loads((ROOT / 'docs/evidence/agent-platform/readback/agents.stdout').read_bytes())['items']
        self.policy = (ROOT / 'agents/response-policy.json').read_bytes()

    def test_only_agent_version_prompt_and_policy_annotation_change(self):
        """All pools, resources, tools, namespaces, network policies and native object identities remain byte-equivalent in value."""
        before = deepcopy(self.original); rendered = RENDERER.render(before, self.policy)
        self.assertEqual(before, self.original)
        for old, new in zip(self.original, rendered):
            if old.get('kind') != 'SandboxAgent': self.assertEqual(old, new); continue
            self.assertEqual(new['spec']['version'], '0.2.2')
            self.assertIn('JSON', new['spec']['declarative']['systemMessage'])
            restored = deepcopy(new); restored['spec']['version'] = old['spec']['version']
            restored['spec']['declarative']['systemMessage'] = old['spec']['declarative']['systemMessage']
            restored['metadata']['annotations'].pop('iot.coursework/response-policy-sha256')
            if 'annotations' not in old['metadata']: restored['metadata'].pop('annotations')
            self.assertEqual(old, restored)

    def test_incomplete_foreign_or_duplicate_agent_set_fails(self):
        """Refuse partial release rewrites or accidental application to an unrelated agent/namespace."""
        for mode in ['missing', 'duplicate', 'foreign-name', 'foreign-namespace']:
            docs = deepcopy(self.original); agents = [v for v in docs if v.get('kind') == 'SandboxAgent']
            if mode == 'missing': docs.remove(agents[0])
            elif mode == 'duplicate': docs.append(deepcopy(agents[0]))
            elif mode == 'foreign-name': agents[0]['metadata']['name'] = 'retrieval'
            else: agents[0]['metadata']['namespace'] = 'another-namespace'
            with self.subTest(mode=mode), self.assertRaises(ValueError): RENDERER.render(docs, self.policy)

    def test_empty_or_unreviewed_policy_fails(self):
        """An absent prompt or unreviewed role/version cannot be promoted by the renderer."""
        for mode in ['empty', 'version', 'role']:
            policy = json.loads(self.policy)
            if mode == 'empty': policy['prompts']['retrieval'] = ''
            elif mode == 'version': policy['version'] = 'unreviewed'
            else: policy['prompts']['actuator'] = 'operate hardware'
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                RENDERER.render(self.original, json.dumps(policy).encode())
