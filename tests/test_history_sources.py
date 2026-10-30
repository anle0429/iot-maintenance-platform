"""Exercise authenticated history mapping against real Git objects without changing captured evidence."""
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
import json, subprocess, tempfile, unittest
from scripts import git_sources

ROOT = Path(__file__).resolve().parents[1]


class HistorySourceTests(unittest.TestCase):
    """Keep mapping, binary source reads and navigation transformations within their declared boundaries."""
    @classmethod
    def setUpClass(cls):
        """Create original and filtered commits, deliberately retaining the original object as a negative boundary."""
        (ROOT/'outputs').mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix='history-source-', dir=ROOT/'outputs')
        cls.root = Path(cls.temporary.name).resolve()
        subprocess.run(['git', 'init', '-q', str(cls.root)], check=True)
        cls.body = b'original\x00binary\r\n\xff\n'
        (cls.root/'kept.bin').write_bytes(cls.body); (cls.root/'private.md').write_bytes(b'private fixture')
        subprocess.run(['git', 'add', '.'], cwd=cls.root, check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-qm', 'Original fixture'], cwd=cls.root, check=True)
        cls.old = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=cls.root, text=True).strip()
        subprocess.run(['git', 'rm', '-q', 'private.md'], cwd=cls.root, check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-qm', 'Filtered fixture'], cwd=cls.root, check=True)
        cls.new = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=cls.root, text=True).strip()
        cls.manifest = cls.root/'docs/history-rewrite/manifest.json'; cls.manifest.parent.mkdir(parents=True)
        cls.raw = json.dumps({'format': 'iot-filtered-history-v1', 'original_commit_count': 1,
                             'commits': {cls.old: cls.new}}).encode()
        cls.manifest.write_bytes(cls.raw)

    @classmethod
    def tearDownClass(cls):
        """Remove only the owned resolved fixture under ignored outputs."""
        assert cls.root.is_relative_to((ROOT/'outputs').resolve())
        cls.temporary.cleanup()

    def setUp(self):
        """Pin this fixture manifest independently for each test and clear cached immutable type checks."""
        self.manifest.write_bytes(self.raw); git_sources.require_commit.cache_clear()
        self.pin = patch.object(git_sources, 'HISTORY_MANIFEST_SHA256', sha256(self.raw).hexdigest())
        self.pin.start(); self.addCleanup(self.pin.stop)

    def test_original_identity_always_uses_filtered_commit(self):
        """Resolve the declared map even when an old commit is still readable in this isolated fixture."""
        self.assertEqual(git_sources.resolve_commit(self.root, self.old), self.new)
        self.assertEqual(git_sources.resolve_revision(self.root, self.old), self.new)
        self.assertEqual(git_sources.resolve_revision(self.root, 'HEAD'), self.new)
        self.assertEqual(git_sources.historical_blob(self.root, self.old+':kept.bin'), self.body)
        with self.assertRaises(ValueError): git_sources.historical_blob(self.root, self.old+':private.md')

    def test_tampered_inventory_and_moving_refs_fail(self):
        """Reject mutable branch names for evidence and manifest modifications after the first successful read."""
        self.assertEqual(git_sources.resolve_commit(self.root, self.old), self.new)
        self.manifest.write_bytes(self.raw+b' ')
        with self.assertRaises(ValueError): git_sources.resolve_commit(self.root, self.old)
        self.manifest.write_bytes(self.raw)
        for ref in ['HEAD', self.old[:8], '0'*40]:
            with self.subTest(ref=ref):
                with self.assertRaises(ValueError): git_sources.resolve_commit(self.root, ref)

    def test_missing_project_inventory_fails_closed(self):
        """The real project cannot silently fall back to unfiltered identities if its migration map disappears."""
        self.manifest.unlink()
        with patch.object(git_sources, 'PROJECT_ROOT', self.root):
            with self.assertRaises(ValueError): git_sources.resolve_commit(self.root, self.old)

    def test_missing_or_noncommit_target_fails(self):
        """A validly pinned map still cannot substitute a blob object or unavailable commit for a commit."""
        blob = subprocess.check_output(['git', 'rev-parse', self.new+':kept.bin'], cwd=self.root, text=True).strip()
        for target in [blob, '0'*40]:
            raw = self.raw.replace(self.new.encode(), target.encode()); self.manifest.write_bytes(raw)
            with patch.object(git_sources, 'HISTORY_MANIFEST_SHA256', sha256(raw).hexdigest()):
                with self.assertRaises(ValueError): git_sources.resolve_commit(self.root, self.old)

    def test_only_own_historical_source_urls_change(self):
        """Leave external URLs, current identities and non-URL commit text byte-exact."""
        own = b'https://github.com/anle0429/iot-maintenance-platform/blob/'
        original = own+self.old[:9].encode()+b'/kept.bin external https://github.com/other/repo/blob/'+self.old.encode()+b'/x '+self.old.encode()
        expected = original.replace(own+self.old[:9].encode(), own+self.new.encode())
        self.assertEqual(git_sources.rewrite_project_links(original, self.root), expected)
        self.assertEqual(git_sources.rewrite_project_links(expected, self.root), expected)


if __name__ == '__main__': unittest.main()
