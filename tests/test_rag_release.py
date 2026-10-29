"""Check that Airflow accepts only prepared run IDs, immutable images and source-sealed files."""
from pathlib import Path
from hashlib import sha256
import json
import tempfile
import unittest
from rag.airflow_tasks import load_release, execute


class RagReleaseTests(unittest.TestCase):
    """Reject escaped paths and changed code before a native container can be launched."""
    def test_invalid_run_and_operation_are_rejected_without_runtime(self):
        """Malformed identifiers and operations must fail before importing Docker or reading secrets."""
        for run in ['../outside', 'rag-x/../../bad', 'other-run']:
            with self.subTest(run=run), self.assertRaises(ValueError): load_release(Path('.'), run)
        with self.assertRaises(ValueError): execute('shell', 'rag-valid')

    def test_valid_seal_then_changed_source_and_image_are_rejected(self):
        """A trusted valid release becomes invalid when either source bytes or its image identity changes."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'data/llm-source/run/tree'; source.mkdir(parents=True)
            (source / 'code.py').write_bytes(b'print(1)')
            manifest = {'run_id': 'rag-valid', 'schema_version': 1, 'source_directory': 'data/llm-source/run/tree',
                        'source_files': {'code.py': sha256(b'print(1)').hexdigest()}, 'image_id': 'sha256:'+'a'*64,
                        'metadata_image_id': 'sha256:'+'b'*64, 'metadata_network_id': 'c'*64}
            path = root / 'data/rag-releases/rag-valid.json'; path.parent.mkdir(); path.write_text(json.dumps(manifest))
            self.assertEqual(load_release(root, 'rag-valid')[1], source.resolve())
            (source / 'code.py').write_bytes(b'print(2)')
            with self.assertRaises(ValueError): load_release(root, 'rag-valid')
            (source / 'code.py').write_bytes(b'print(1)'); manifest['image_id'] = 'untrusted:latest'; path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError): load_release(root, 'rag-valid')
