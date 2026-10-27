"""Prove cache acceleration preserves source bytes, physical histories and full semantic validation."""

from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
import importlib.util

from generator.chunked import ChunkConfig, generate_chunked
from generator.chunk_quality import inspect_chunked
from generator.simulation import physical_identity
from pipelines.source_runtime import CachedChunkConfig, fast_json, timestamp_caches


class SourceRuntimeTests(unittest.TestCase):
    """The optimization may save CPU work but must never create another synthetic dataset."""

    def test_cached_config_keeps_identity_and_fields(self):
        """Cached attributes must not enter the serialized config or alter labels/events."""
        ordinary = ChunkConfig(machine_count=2, duration_hours=1)
        cached = CachedChunkConfig(**ordinary.to_dict())
        for _ in range(3):
            self.assertEqual(cached.start, ordinary.start)
            self.assertEqual(cached.end, ordinary.end)
        self.assertEqual(asdict(cached), asdict(ordinary))
        self.assertEqual(physical_identity(cached), physical_identity(ordinary))

    def test_all_source_bytes_and_validation_are_identical(self):
        """Exercise duplicates, late rows, both firmware versions and stream ordering across small parts."""
        config = ChunkConfig(machine_count=3, duration_hours=2, offline_duplicate_rate=0.13, late_rate=0.17)
        with tempfile.TemporaryDirectory() as temporary:
            ordinary, cached = Path(temporary) / 'ordinary', Path(temporary) / 'cached'
            generate_chunked(config, ordinary, chunk_rows=17)
            expected = inspect_chunked(ordinary)
            with timestamp_caches():
                generate_chunked(CachedChunkConfig(**config.to_dict()), cached, chunk_rows=17)
                actual = inspect_chunked(cached)
            self.assertEqual(expected, actual)
            self.assertEqual({p.relative_to(ordinary).as_posix(): p.read_bytes() for p in ordinary.rglob('*') if p.is_file()},
                             {p.relative_to(cached).as_posix(): p.read_bytes() for p in cached.rglob('*') if p.is_file()})

    def test_aliases_restore_after_failure(self):
        """A failed optimization scope must not leave other callers with patched validator globals."""
        import generator.chunk_quality as quality
        import generator.simulation as simulation
        before = (quality.ChunkConfig, quality.parse_ts, simulation.iso_ts)
        with self.assertRaisesRegex(RuntimeError, 'injected'):
            with timestamp_caches():
                raise RuntimeError('injected')
        self.assertEqual(before, (quality.ChunkConfig, quality.parse_ts, simulation.iso_ts))

    @unittest.skipUnless(importlib.util.find_spec('orjson'), 'optional scale codec not installed')
    def test_native_codec_keeps_full_source_bytes_and_rules(self):
        """Compare complete generated files and semantic reports, including stream-envelope fallback."""
        config = ChunkConfig(machine_count=3, duration_hours=2, offline_duplicate_rate=0.13, late_rate=0.17)
        with tempfile.TemporaryDirectory() as temporary:
            ordinary, accelerated = Path(temporary) / 'ordinary', Path(temporary) / 'accelerated'
            generate_chunked(config, ordinary, chunk_rows=17)
            expected = inspect_chunked(ordinary)
            with timestamp_caches(), fast_json(True):
                generate_chunked(CachedChunkConfig(**config.to_dict()), accelerated, chunk_rows=17)
                actual = inspect_chunked(accelerated)
            self.assertEqual(expected, actual)
            self.assertEqual({p.relative_to(ordinary).as_posix(): p.read_bytes() for p in ordinary.rglob('*') if p.is_file()},
                             {p.relative_to(accelerated).as_posix(): p.read_bytes() for p in accelerated.rglob('*') if p.is_file()})

    @unittest.skipUnless(importlib.util.find_spec('orjson'), 'optional scale codec not installed')
    def test_native_codec_preserves_strict_fallbacks(self):
        """Nonfinite, exponent, Unicode, nested and oversized inputs must retain the source contract."""
        import generator.chunked as chunked
        import generator.chunk_quality as quality
        examples = [{'number': 1e20}, {'number': 0.00001}, {'text': 'bơm'}, {'event': {'value': 2.0}},
                    {'negative_zero': -0.0}, {'value': 1234.567, 'nullable': None}]
        expected = [chunked.encoded(row) for row in examples]
        before = chunked.encoded, quality.records
        with fast_json(True):
            self.assertEqual([chunked.encoded(row) for row in examples], expected)
            with self.assertRaises(ValueError):
                chunked.encoded({'sensor': float('nan')})
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'bad.jsonl'
                for content in (b'[]\n', b'{}', b' ' * 16_385 + b'\n'):
                    path.write_bytes(content)
                    with self.assertRaises(ValueError):
                        list(quality.records(path))
        self.assertEqual(before, (chunked.encoded, quality.records))


if __name__ == '__main__':
    unittest.main()
