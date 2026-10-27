"""Verify small known-answer version histories, tombstones, idempotency and corruption refusal."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from ml.data_versions import ContentAddressedDatasetStore,relative_name


class DataVersionsTest(unittest.TestCase):
    """Check storage and restoration behavior rather than private implementation details."""
    def test_incremental_replay_change_add_delete_and_restore(self):
        with TemporaryDirectory() as temp:
            base=Path(temp);source=base/'input';source.mkdir()
            for n,b in [('a',b'one'),('b',b'two'),('c',b'three')]: (source/n).write_bytes(b)
            store=ContentAddressedDatasetStore(base/'store');files={'day=1/a':source/'a','day=2/b':source/'b'}
            first=store.publish(files,{'protocol':'fixed'})
            self.assertEqual((first['new_blob_count'],first['new_blob_bytes']),(2,6))
            replay=store.publish(dict(reversed(list(files.items()))),{'protocol':'fixed'},first['version'])
            self.assertEqual(replay['version'],first['version']);self.assertEqual(replay['new_blob_bytes'],0)
            (source/'b').write_bytes(b'updated')
            changed=store.publish({'day=2/b':source/'b','day=3/c':source/'c'},{'protocol':'fixed'},first['version'])
            self.assertEqual(changed['deleted_paths'],['day=1/a']);self.assertEqual(changed['new_blob_bytes'],12)
            self.assertEqual(changed['new_blob_count'],2)
            for receipt,expected in [(first,{'day=1/a':b'one','day=2/b':b'two'}),(changed,{'day=2/b':b'updated','day=3/c':b'three'})]:
                target=base/receipt['version'];self.assertTrue(store.restore(receipt['version'],target)['passed'])
                self.assertEqual({p.relative_to(target).as_posix():p.read_bytes() for p in target.rglob('*') if p.is_file()},expected)
            with self.assertRaises(FileExistsError):store.restore(first['version'],base/first['version'])

    def test_corruption_and_unsafe_names_are_rejected(self):
        with TemporaryDirectory() as temp:
            base=Path(temp);source=base/'input';source.write_bytes(b'valid')
            store=ContentAddressedDatasetStore(base/'store');first=store.publish({'data.parquet':source},{})
            store.blob(first['new_blob_sha256'][0]).write_bytes(b'corrupt')
            with self.assertRaises(ValueError):store.restore(first['version'],base/'restore')
            with self.assertRaises(ValueError):store.publish({'data.parquet':source},{})
            self.assertFalse((base/'restore').exists())
            for name in ['../outside','/absolute','a\\b','a//b','C:/bad','./a','a/../b']:
                with self.assertRaises(ValueError):relative_name(name)

    def test_same_content_under_two_names_is_stored_once(self):
        with TemporaryDirectory() as temp:
            base=Path(temp);source=base/'input';source.write_bytes(b'shared')
            receipt=ContentAddressedDatasetStore(base/'store').publish({'a':source,'b':source},{})
            self.assertEqual(receipt['logical_bytes'],12);self.assertEqual(receipt['new_blob_bytes'],6)
            self.assertEqual(receipt['new_blob_count'],1)


if __name__=='__main__':unittest.main()
