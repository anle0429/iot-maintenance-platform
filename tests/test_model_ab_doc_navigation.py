"""Keep historical model-study inputs exact while allowing one named footer-link relocation."""
from pathlib import Path
import importlib.util, sys, unittest
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT/'scripts'))
SPEC = importlib.util.spec_from_file_location('model_ab_verifier', ROOT/'scripts/verify-llm-model-ab.py')
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)


class NavigationBoundaryTests(unittest.TestCase):
    """Reject contract edits and undeclared source changes rather than broadly ignoring Markdown differences."""
    def test_exact_source_and_only_named_link(self):
        """Accept exact bytes or one precise footer change in the two declared input documents."""
        old = b'target=(t,t+1h]\n[Demo and recovery](demo-walkthrough.md)\n'
        new = old.replace(b'(demo-walkthrough.md)', b'(operations.md)')
        self.assertTrue(MODULE.current_source_matches('llm/ab_experiment.py', old, old))
        for name in ['docs/data-contract.md', 'docs/ml-evaluation-protocol.md']:
            self.assertTrue(MODULE.current_source_matches(name, old, new))

    def test_filtered_source_url_and_footer_only(self):
        """Accept mapped own-repository source URLs while rejecting changed contract text or foreign URLs."""
        from git_sources import history_manifest, rewrite_project_links
        old_id = next(iter(history_manifest(ROOT)['commits']))
        old = (b'target=(t,t+1h]\nhttps://github.com/anle0429/iot-maintenance-platform/blob/'
               + old_id.encode() + b'/docs/data-contract.md\n[Demo and recovery](demo-walkthrough.md)\n')
        new = rewrite_project_links(old).replace(b'(demo-walkthrough.md)', b'(operations.md)')
        for name in ['docs/data-contract.md', 'docs/ml-evaluation-protocol.md']:
            self.assertTrue(MODULE.current_source_matches(name, old, new))
            self.assertFalse(MODULE.current_source_matches(name, old, new.replace(b'1h', b'2h')))
            self.assertFalse(MODULE.current_source_matches(name, old, new.replace(b'anle0429', b'other')))
        self.assertFalse(MODULE.current_source_matches('llm/ab_experiment.py', old, new))

    def test_contract_edits_and_other_paths_are_rejected(self):
        """A changed horizon, extra text, repeated footer or another file cannot use the navigation exception."""
        old = b'target=(t,t+1h]\n[Demo and recovery](demo-walkthrough.md)\n'
        new = old.replace(b'(demo-walkthrough.md)', b'(operations.md)')
        for changed in [new.replace(b'1h', b'2h'), new+b'extra', new.replace(b'operations.md', b'other.md')]:
            self.assertFalse(MODULE.current_source_matches('docs/data-contract.md', old, changed))
        self.assertFalse(MODULE.current_source_matches('llm/ab_experiment.py', old, new))
        self.assertFalse(MODULE.current_source_matches('docs/data-contract.md', old+old, new+new))


if __name__ == '__main__': unittest.main()
