"""Bound pure timestamp/config caches to one isolated offline generation or validation process."""

from contextlib import contextmanager
from functools import cached_property, lru_cache
import math

from generator.chunked import ChunkConfig
from generator.config import parse_ts


class CachedChunkConfig(ChunkConfig):
    """Cache immutable timestamps without changing dataclass fields, config JSON or physical IDs."""

    @cached_property
    def start(self):
        """Parse the same immutable UTC start once instead of once per reading."""
        return super().start

    @cached_property
    def end(self):
        """Cache the immutable exclusive end used by simulation and interval checks."""
        return super().end


@contextmanager
def timestamp_caches(enabled: bool = True):
    """Memoize repeated pure conversions with a fixed 4096-entry bound, restoring aliases on exit."""
    if not enabled:
        yield
        return
    import generator.chunked as chunked
    import generator.chunk_quality as quality
    import generator.simulation as simulation
    originals = [(module, name, getattr(module, name)) for module, name in
                 ((chunked, 'iso_ts'), (quality, 'iso_ts'), (simulation, 'iso_ts'),
                  (quality, 'parse_ts'), (quality, 'ChunkConfig'))]
    iso = lru_cache(maxsize=4096)(chunked.iso_ts)
    parsed = lru_cache(maxsize=4096)(parse_ts)
    try:
        for module in (chunked, quality, simulation):
            module.iso_ts = iso
        quality.parse_ts = parsed
        quality.ChunkConfig = CachedChunkConfig
        yield
    finally:
        for module, name, value in originals:
            setattr(module, name, value)
        iso.cache_clear()
        parsed.cache_clear()


@contextmanager
def fast_json(enabled: bool = False):
    """Accelerate bounded JSONL IO, retaining stdlib fallbacks for non-domain numeric/text/container values."""
    if not enabled:
        yield
        return
    import orjson
    import generator.chunked as chunked
    import generator.chunk_quality as quality
    original_encoded, original_records = chunked.encoded, quality.records

    def encode(row: dict) -> bytes:
        """Keep canonical bytes for generated ASCII scalar rows; unusual values use the original strict encoder."""
        for value in row.values():
            if isinstance(value, float):
                if not math.isfinite(value) or (value and (abs(value) < 1e-4 or abs(value) >= 1e16)):
                    return original_encoded(row)
            elif isinstance(value, (dict, list)) or (isinstance(value, str) and not value.isascii()):
                return original_encoded(row)
        return orjson.dumps(row, option=orjson.OPT_SORT_KEYS | orjson.OPT_APPEND_NEWLINE)

    def records(path):
        """Retain the original 16KiB/one-object-per-line safety contract while parsing with the pinned native decoder."""
        with path.open('rb') as source:
            while line := source.readline(16_385):
                quality.require(len(line) <= 16_384 and line.endswith(b'\n'), 'oversized/unterminated row')
                value = orjson.loads(line)
                quality.require(isinstance(value, dict), 'records must be JSON objects')
                yield value
    try:
        chunked.encoded, quality.records = encode, records
        yield
    finally:
        chunked.encoded, quality.records = original_encoded, original_records
