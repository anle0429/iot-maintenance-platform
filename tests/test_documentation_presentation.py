"""Keep figure layout edits from silently replacing historical text or measured values."""
import subprocess
import unittest
from scripts.documentation_presentation import ROOT, before_presentation, presentation


class DocumentationPresentationTests(unittest.TestCase):
    """Reject unreviewed content changes while authenticating the declared figure layout."""

    def test_all_declared_pages_restore_exact_predecessors(self):
        """Recover all seven unchanged predecessor pages after exact patch validation."""
        value=presentation()
        for name in value['files']:
            original=subprocess.check_output(['git','show',value['base_commit']+':'+name],cwd=ROOT)
            self.assertEqual(before_presentation(name,(ROOT/name).read_bytes()),original,name)

    def test_changed_measurement_or_interpretation_is_rejected(self):
        """Reject both a changed timing and a changed description of the dedup decision."""
        name='docs/edai-1/batch_corrections_benchmark.md'
        body=(ROOT/name).read_bytes()
        for before,after in [(b'0.926981',b'9.926981'),(b'window',b'promoted')]:
            self.assertIn(before,body)
            with self.assertRaises(AssertionError):
                before_presentation(name,body.replace(before,after,1))

    def test_undeclared_document_is_not_normalized(self):
        """Leave undeclared content untouched so historical checks remain strict."""
        body=b'Unreviewed content\r\n'
        self.assertEqual(before_presentation('docs/unreviewed.md',body),body)


if __name__=='__main__':unittest.main()
