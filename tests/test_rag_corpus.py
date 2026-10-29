"""Verify source provenance, chunk identity, coverage and path/size boundaries using independently constructed documents."""
from hashlib import sha256
from pathlib import Path
import json
import tempfile
import unittest
from rag.corpus import read_catalog, chunk_documents


class CorpusContractTests(unittest.TestCase):
    """Keep retrieval inputs reproducible and reject changed or escaped sources before model inference."""
    def test_authored_catalog_is_readable_and_chunk_ids_are_stable(self):
        """The actual authored reference corpus must preserve all four source identities across replay."""
        documents = read_catalog(Path(__file__).resolve().parents[1] / 'knowledge')
        first = chunk_documents(documents); second = chunk_documents(documents)
        self.assertEqual(len(documents), 4); self.assertEqual(first, second)
        self.assertEqual({row['document_id'] for row in first}, {row['document_id'] for row in documents})
        self.assertTrue(all(row['text'] and len(row['text']) <= 1100 for row in first))

    def test_changed_and_duplicate_source_are_rejected(self):
        """Content changes require explicit source version/hash review rather than silent reindexing."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); raw = b'# Title\n\nOriginal text.'; (root / 'test.md').write_bytes(raw)
            row = {'document_id': 'test-doc', 'revision': 1, 'source_type': 'authored-synthetic-reference', 'file': 'test.md', 'sha256': sha256(raw).hexdigest()}
            for rows in [[dict(row, sha256='0'*64)], [row, row], [dict(row, file='../escaped.md')]]:
                (root / 'catalog.json').write_text(json.dumps({'schema_version': 1, 'documents': rows}))
                with self.subTest(rows=rows), self.assertRaises(ValueError): read_catalog(root)

    def test_long_section_preserves_every_character_with_bounded_overlap(self):
        """Overlapping slices must cover an independent no-space source without gaps or oversized chunks."""
        text = '# Heading\n' + ''.join(str(i % 10) for i in range(1500))
        document = {'document_id': 'long-doc', 'revision': 1, 'sha256': sha256(text.encode()).hexdigest(), 'file': 'long.md', 'text': text}
        rows = chunk_documents([document], max_chars=200, overlap_chars=20)
        rebuilt = rows[0]['text'] + ''.join(row['text'][20:] for row in rows[1:])
        self.assertEqual(rebuilt, text); self.assertEqual(len({row['chunk_id'] for row in rows}), len(rows))
        self.assertTrue(all(len(row['text']) <= 200 for row in rows))

    def test_invalid_chunk_boundaries_are_rejected(self):
        """Both size limits and the overlap proportion must be enforced."""
        for size, overlap in [(199, 0), (4001, 0), (200, -1), (200, 100)]:
            with self.subTest(size=size, overlap=overlap), self.assertRaises(ValueError): chunk_documents([], size, overlap)
