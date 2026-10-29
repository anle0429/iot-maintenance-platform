"""Reject misleading CPU/GPU comparisons and inadequate dedicated-worker budgets without provisioning cloud resources."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import json
import tempfile
import unittest
from llm.gpu_benchmark import comparison, resource_gate, verified_source


def batches():
    """Create paired synthetic measurements for arithmetic tests, never described as native GPU evidence."""
    values = []
    for elapsed in [10.0, 2.0]:
        row = {'status': 200, 'text': 'fixture answer', 'ttft_seconds': elapsed / 2, 'elapsed_seconds': elapsed,
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30,
                      'prompt_tokens_details': {'cached_tokens': 0}}}
        measured = [dict(deepcopy(row), repeat=r, prompt_index=p) for r in range(3) for p in range(3)]
        values.append({'parameters': {'seed': 7, 'cache_prompt': False}, 'workload': ['a', 'b', 'c'],
            'warmup': [deepcopy(row) for _ in range(3)], 'measured': measured})
    return values


class GpuBenchmarkTests(unittest.TestCase):
    """Keep performance arithmetic, output equality and the physical execution claim separate."""

    def test_paired_arithmetic_and_independent_text_equality(self):
        """A fivefold fixture speedup cannot imply that distinct generated answers are identical."""
        cpu, gpu = batches(); report = comparison(cpu, gpu)
        self.assertEqual(report['elapsed_speedup_ratio'], 5)
        self.assertEqual(report['ttft_speedup_ratio'], 5)
        self.assertEqual(report['cpu']['reported_completion_tokens'], 90)
        self.assertTrue(report['paired_texts_identical'])
        gpu['measured'][0]['text'] = 'different answer'
        self.assertFalse(comparison(cpu, gpu)['paired_texts_identical'])

    def test_unpaired_or_changed_workloads_are_rejected(self):
        """Changing seed, prompt order, request count or warm-up count invalidates the comparison."""
        for mode in ['parameters', 'workload', 'measured-count', 'warmup-count', 'order']:
            cpu, gpu = batches()
            if mode == 'parameters': gpu['parameters']['seed'] = 8
            elif mode == 'workload': gpu['workload'].reverse()
            elif mode == 'measured-count': gpu['measured'].pop()
            elif mode == 'warmup-count': gpu['warmup'].pop()
            else: gpu['measured'].reverse()
            with self.subTest(mode=mode), self.assertRaises(ValueError): comparison(cpu, gpu)

    def test_invalid_or_cached_measurements_are_rejected(self):
        """Missing usage, cached prompt tokens and invalid timing cannot produce a speedup claim."""
        for mode in ['cached', 'no-cache-receipt', 'missing-usage', 'zero-tokens', 'boolean-tokens', 'wrong-total',
                     'empty-text', 'http-error', 'zero-ttft', 'inverted-times', 'infinite-time', 'nan-time']:
            cpu, gpu = batches(); row = gpu['measured'][0]
            if mode == 'cached': row['usage']['prompt_tokens_details']['cached_tokens'] = 1
            elif mode == 'no-cache-receipt': row['usage'].pop('prompt_tokens_details')
            elif mode == 'missing-usage': row.pop('usage')
            elif mode == 'zero-tokens': row['usage']['completion_tokens'] = 0
            elif mode == 'boolean-tokens': row['usage']['completion_tokens'] = True
            elif mode == 'wrong-total': row['usage']['total_tokens'] = 31
            elif mode == 'empty-text': row['text'] = ''
            elif mode == 'http-error': row['status'] = 503
            elif mode == 'zero-ttft': row['ttft_seconds'] = 0
            elif mode == 'inverted-times': row['ttft_seconds'] = 3
            elif mode == 'infinite-time': row['elapsed_seconds'] = float('inf')
            else: row['elapsed_seconds'] = float('nan')
            with self.subTest(mode=mode), self.assertRaises(ValueError): comparison(cpu, gpu)

    def test_each_resource_threshold_and_dedicated_root(self):
        """Every resource edge is enforced independently, including the eight-hour upper bound."""
        now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
        cases = [(2700, 10, 15, True), (28800, 10, 15, True), (2699, 10, 15, False),
                 (28801, 10, 15, False), (3600, 9, 15, False), (3600, 10, 14, False)]
        for seconds, ram, disk, accepted in cases:
            args = [Path('/srv/iot-inference'), now + timedelta(seconds=seconds), ram * 1024**3, disk * 1024**3, now]
            with self.subTest(seconds=seconds, ram=ram, disk=disk):
                if accepted: self.assertEqual(resource_gate(*args)['remaining_session_seconds'], seconds)
                else:
                    with self.assertRaises(ValueError): resource_gate(*args)
        with self.assertRaises(ValueError): resource_gate(Path('/srv/iot-data'), now + timedelta(hours=1), 20 * 1024**3, 30 * 1024**3, now)

    def test_sealed_inputs_reject_modified_missing_or_escaped_members(self):
        """A matching checksum outside the approved tree is still an invalid source."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve(); source = base / 'tree'; source.mkdir()
            names = ['llm/__init__.py', 'llm/benchmark.py', 'llm/gpu_benchmark.py',
                     'infra/llm/assistant.lock.json', 'infra/llm/gpu-runtime.lock.json']
            for name in names:
                path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')
            seal = {'files': {name: sha256(b'fixture').hexdigest() for name in names}}
            manifest = base / 'manifest.json'; manifest.write_text(json.dumps(seal))
            self.assertEqual(verified_source(source), seal)
            (source / names[0]).write_bytes(b'changed')
            with self.assertRaises(ValueError): verified_source(source)
            (source / names[0]).write_bytes(b'fixture')
            (base / 'outside').write_bytes(b'fixture'); seal['files']['../outside'] = sha256(b'fixture').hexdigest()
            manifest.write_text(json.dumps(seal))
            with self.assertRaises(ValueError): verified_source(source)
            seal['files'].pop('../outside'); seal['files'].pop(names[0]); manifest.write_text(json.dumps(seal))
            with self.assertRaises(ValueError): verified_source(source)


if __name__ == '__main__':
    unittest.main()
