"""Check path-only Git Merkle reconstruction and rejection of corrupt or unsafe original metadata proofs."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tarfile, tempfile, unittest
from scripts.history_proof import object_id, tree_entries, filtered_tree, rewritten_commit, original_objects

ROOT = Path(__file__).resolve().parents[1]


def entry(mode, name, identity):
    """Encode one exact binary Git tree entry for the isolated proof fixture."""
    return mode+b' '+name+b'\0'+bytes.fromhex(identity)


class HistoryProofTests(unittest.TestCase):
    """Ensure the provenance proof cannot conceal retained source or original commit metadata changes."""
    def test_only_removed_path_and_parent_tree_change(self):
        """Preserve binary identities, executable modes, dates, encoding and message bytes exactly."""
        kept = object_id('blob', b'kept\x00\xff'); removed = object_id('blob', b'personal')
        docs = entry(b'100644', b'private.md', removed)+entry(b'100755', b'tool.py', kept)
        docs_id = object_id('tree', docs)
        root = entry(b'40000', b'docs', docs_id)+entry(b'100644', b'kept.bin', kept)
        root_id = object_id('tree', root); objects = {(root_id, 'tree'): root, (docs_id, 'tree'): docs}
        expected_docs = object_id('tree', entry(b'100755', b'tool.py', kept))
        expected_tree = object_id('tree', entry(b'40000', b'docs', expected_docs)+entry(b'100644', b'kept.bin', kept))
        self.assertEqual(filtered_tree(root_id, objects, {'docs/private.md'}), expected_tree)
        parent = '1'*40; mapped = '2'*40
        metadata = b'\nauthor Person <p@example.invalid> 123 +0700\ncommitter Person <p@example.invalid> 456 -0500\nencoding UTF-8\n\nOriginal message '+parent.encode()+b'\n'
        original = b'tree '+root_id.encode()+b'\nparent '+parent.encode()+metadata
        result = rewritten_commit(original, {parent: mapped}, objects, {'docs/private.md'})
        self.assertEqual(result, b'tree '+expected_tree.encode()+b'\nparent '+mapped.encode()+metadata)

    def test_empty_nested_tree_and_malformed_entry(self):
        """Drop an emptied nested directory while retaining an empty root and rejecting truncated identities."""
        blob = object_id('blob', b'x'); raw = entry(b'100644', b'private.md', blob); identity = object_id('tree', raw)
        objects = {(identity, 'tree'): raw}
        self.assertIsNone(filtered_tree(identity, objects, {'docs/private.md'}, 'docs/'))
        self.assertEqual(filtered_tree(identity, objects, {'private.md'}), object_id('tree', b''))
        for bad in [raw[:-1], entry(b'100644', b'bad/name', blob)]:
            with self.assertRaises(ValueError): tree_entries(bad)

    def test_archive_rejects_blobs_links_traversal_and_corruption(self):
        """Even re-inventoried fixtures must be regular commit/tree objects with authentic Git identities."""
        (ROOT/'outputs').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='history-proof-', dir=ROOT/'outputs') as temporary:
            root = Path(temporary).resolve(); assert root.is_relative_to((ROOT/'outputs').resolve())
            folder = root/'docs/history-rewrite'; folder.mkdir(parents=True)
            body = b''; good = object_id('tree', body)+'.tree'
            for name, content, link in [(good, body, False), ('../'+good, body, False),
                                        (object_id('blob', body)+'.blob', body, False),
                                        (good, b'corrupt', False), (good, body, True)]:
                stream = BytesIO()
                with tarfile.open(fileobj=stream, mode='w:gz') as archive:
                    member = tarfile.TarInfo(name); member.size = len(content)
                    if link: member.type = tarfile.SYMTYPE; member.linkname = 'elsewhere'
                    archive.addfile(member, BytesIO(content))
                raw = stream.getvalue(); (folder/'proof.tar.gz').write_bytes(raw)
                manifest = {'archive': 'proof.tar.gz', 'archive_bytes': len(raw), 'archive_sha256': sha256(raw).hexdigest(),
                            'original_objects': {name: {'bytes': len(content), 'sha256': sha256(content).hexdigest()}}}
                if name == good and content == body and not link:
                    self.assertEqual(original_objects(root, manifest), {(object_id('tree', body), 'tree'): body})
                    manifest['archive_sha256'] = '0'*64
                with self.assertRaises(ValueError): original_objects(root, manifest)


if __name__ == '__main__': unittest.main()
