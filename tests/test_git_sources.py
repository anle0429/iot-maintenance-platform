"""Exercise real historical binary, empty and Unicode Git objects and invalid source selections."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from scripts.git_sources import git_blobs

ROOT = Path(__file__).resolve().parents[1]


class HistoricalSourceTests(unittest.TestCase):
    """Prove source verification reads committed bytes rather than mutable working files or a moving branch."""
    @classmethod
    def setUpClass(cls):
        """Create an isolated real repository under the project's ignored output directory."""
        (ROOT/'outputs').mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix='git-source-test-', dir=ROOT/'outputs')
        cls.fixture = Path(cls.temporary.name).resolve()
        assert cls.fixture.is_relative_to((ROOT/'outputs').resolve())
        subprocess.run(['git', 'init', '-q', '-b', 'codex/source-fixture', str(cls.fixture)], check=True)
        cls.original = {'binary.bin': bytes(range(256))*3, 'empty.txt': b'',
                        'nested/space Δ name.txt': 'Unicode source: Δ and 漢字.\n'.encode()}
        for name, raw in cls.original.items():
            path = cls.fixture/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        subprocess.run(['git', 'add', '.'], cwd=cls.fixture, check=True)
        subprocess.run(['git', '-c', 'user.name=Source fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-q', '-m', 'Record original source'], cwd=cls.fixture, check=True)
        cls.commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=cls.fixture, text=True).strip()
        (cls.fixture/'binary.bin').write_bytes(b'new working bytes')
        subprocess.run(['git', 'add', 'binary.bin'], cwd=cls.fixture, check=True)
        subprocess.run(['git', '-c', 'user.name=Source fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-q', '-m', 'Move the fixture branch'], cwd=cls.fixture, check=True)
        (cls.fixture/'binary.bin').write_bytes(b'newer uncommitted working bytes')

    @classmethod
    def tearDownClass(cls):
        """Check the resolved owned path again before removing only this temporary test repository."""
        assert cls.fixture.is_relative_to((ROOT/'outputs').resolve())
        cls.temporary.cleanup()

    def test_exact_binary_empty_and_unicode_blobs(self):
        """Preserve NUL/newline framing and non-ASCII filenames/content, including zero-length objects."""
        self.assertEqual(git_blobs(self.fixture, self.commit, self.original), self.original)

    def test_working_copy_does_not_replace_history(self):
        """An edited working file must not change evidence pinned to the earlier commit."""
        self.assertNotEqual((self.fixture/'binary.bin').read_bytes(), self.original['binary.bin'])
        self.assertNotEqual(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.fixture, text=True).strip(), self.commit)
        self.assertEqual(git_blobs(self.fixture, self.commit, ['binary.bin']), {'binary.bin': self.original['binary.bin']})

    def test_invalid_or_missing_sources_fail(self):
        """Reject mutable refs, missing files/trees and ambiguous source expressions before accepting evidence."""
        for commit, names in [('HEAD', ['binary.bin']), (self.commit[:8], ['binary.bin']), (self.commit, ['missing']),
                              (self.commit, ['nested']), (self.commit, ['binary.bin', 'binary.bin']),
                              (self.commit, ['../outside']), (self.commit, ['/absolute']),
                              (self.commit, ['name\nnew request']), (self.commit, ['name\0null']), (self.commit, [])]:
            with self.subTest(commit=commit, names=names):
                with self.assertRaises(ValueError): git_blobs(self.fixture, commit, names)


if __name__ == '__main__': unittest.main()
