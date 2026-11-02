"""Keep documentation moves limited to navigation and retain historical source identities."""
from pathlib import Path
import subprocess
import unittest
from scripts.documentation_paths import ROOT, layout, path_map, relocate_markdown, historical_document, navigation_equivalent


class DocumentationPathTests(unittest.TestCase):
    """Reject content edits and foreign/pinned URL changes during a reviewed folder migration."""

    def test_relative_assets_cross_part_and_current_urls(self):
        """Rebase actual path classes while retaining anchors and existing external source versions."""
        mapping = {'docs/a.md':'docs/edai-1/a.md','docs/b.md':'docs/edai-2/b.md'}
        original = b'[Peer](b.md#section)\n![Plot](images/x.png)\n[Code](../generator/README.md)\n<a href="b.md">B</a>\nhttps://github.com/anle0429/iot-maintenance-platform/blob/main/docs/b.md\nhttps://github.com/anle0429/iot-maintenance-platform/blob/1234567/docs/b.md\nhttps://example.org/docs/b.md\n'
        current = relocate_markdown(original,'docs/a.md',mapping['docs/a.md'],mapping)
        self.assertIn(b'(../edai-2/b.md#section)',current)
        self.assertIn(b'(../images/x.png)',current)
        self.assertIn(b'(../../generator/README.md)',current)
        self.assertIn(b'href="../edai-2/b.md"',current)
        # Bare current URLs are not Markdown links and retain their original text.
        self.assertIn(b'/blob/1234567/docs/b.md',current)
        self.assertIn(b'https://example.org/docs/b.md',current)

    def test_real_source_and_only_declared_navigation(self):
        """Keep the full original contract exact while allowing only the sealed path transformation."""
        name='docs/data-contract.md';original=historical_document(name)
        current=(ROOT/path_map()[name]).read_bytes()
        self.assertTrue(navigation_equivalent(name,original,current))
        self.assertFalse(navigation_equivalent(name,original,current+b'\nChanged contract\n'))
        self.assertFalse(navigation_equivalent('docs/unreviewed.md',original,current))
        self.assertFalse(navigation_equivalent(name,original,current.replace(b'1h',b'2h')))

    def test_complete_original_inventory_without_flat_aliases(self):
        """Require every original root page and a unique destination; no duplicate legacy stub pages."""
        value=layout()
        originals=subprocess.check_output(['git','ls-tree','--name-only',value['base_commit']+':docs'],cwd=ROOT).decode().splitlines()
        expected={'docs/'+name for name in originals if name.endswith('.md') and name!='README.md'}
        self.assertEqual(expected,set(value['files']))
        for old,new in path_map().items():
            self.assertFalse((ROOT/old).exists())
            self.assertTrue((ROOT/new).is_file())


if __name__=='__main__':unittest.main()
