"""Exercise archive traversal, corruption, collision and no-overwrite boundaries using isolated temporary fixtures."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json, tarfile, tempfile, unittest
from scripts.evidence_bundle import checked_index, restore


class BundleTests(unittest.TestCase):
    """Preserve receipt bytes and existing local work without contacting a service or reading real evidence."""
    def setUp(self):
        """Create an isolated workspace and two binary/text receipt fixtures."""
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.archive = self.root/'bundle.tar.gz'
        self.files = {'docs/evidence/example/a.bin': b'\0raw\r\n\xff', 'docs/evidence/example/b.txt': b'original\n'}

    def bundle(self, members=None):
        """Create a small real gzip/tar and inventory; malformed members can differ from the declared fixtures."""
        with tarfile.open(self.archive, 'w:gz') as tar:
            for name, body in (list(self.files.items()) if members is None else members):
                info = tarfile.TarInfo(name); info.size = len(body); tar.addfile(info, BytesIO(body))
        raw = self.archive.read_bytes()
        return {'format': 'iot-recorded-evidence-bundle-v1', 'archive_bytes': len(raw), 'archive_sha256': sha256(raw).hexdigest(),
            'original_payload_bytes': sum(map(len, self.files.values())),
            'files': {n: {'bytes': len(b), 'sha256': sha256(b).hexdigest()} for n, b in self.files.items()}}

    def test_roundtrip_binary_bytes_and_idempotence(self):
        """Restore missing files once and retain exact bytes on a second pass."""
        index = self.bundle(); self.assertEqual(restore(self.root, self.archive, index)['restored_missing_files'], 2)
        self.assertEqual(restore(self.root, self.archive, index)['restored_missing_files'], 0)
        for name, body in self.files.items(): self.assertEqual((self.root/name).read_bytes(), body)

    def test_verify_only_does_not_create_receipts(self):
        """Inspect the full archive without modifying a clean workspace."""
        self.assertTrue(restore(self.root, self.archive, self.bundle(), verify_only=True)['passed'])
        self.assertFalse((self.root/'docs').exists())

    def test_changed_existing_receipt_stops_before_any_write(self):
        """Reject a conflicting local file without replacing it or writing another missing member."""
        index = self.bundle(); path = self.root/next(iter(self.files)); path.parent.mkdir(parents=True); path.write_bytes(b'edited')
        with self.assertRaises(ValueError): restore(self.root, self.archive, index)
        self.assertEqual(path.read_bytes(), b'edited'); self.assertFalse((self.root/'docs/evidence/example/b.txt').exists())

    def test_invalid_paths_and_case_collisions(self):
        """Reject escapes and Windows path ambiguities before opening the archive."""
        index = self.bundle(); pin = next(iter(index['files'].values()))
        for name in ['../outside', '/docs/evidence/x/y', 'docs/evidence/../bad', 'docs/evidence/x/C:bad', 'docs/evidence/x/a\\b', 'docs/evidence/x/file.']:
            row = json.loads(json.dumps(index)); row['files'] = {name: pin}; row['original_payload_bytes'] = pin['bytes']
            with self.subTest(path=name), self.assertRaises(ValueError): checked_index(row)
        row = json.loads(json.dumps(index)); row['files']['docs/evidence/example/A.BIN'] = pin; row['original_payload_bytes'] += pin['bytes']
        with self.assertRaises(ValueError): checked_index(row)

    def test_missing_duplicate_and_corrupt_members_fail_before_write(self):
        """An authenticated container still needs exact member identities, lengths and original body hashes."""
        items = list(self.files.items())
        for members in [items[:1], items+[items[0]], [(items[0][0], b'changed')]+items[1:]]:
            index = self.bundle(members)
            with self.subTest(members=members), self.assertRaises(ValueError): restore(self.root, self.archive, index)
            self.assertFalse((self.root/'docs').exists())


if __name__ == '__main__': unittest.main()
