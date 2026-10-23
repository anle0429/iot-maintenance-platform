"""Check chunk independence, continuous physical truth, bounded failure paths and semantic corruption."""

from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from generator.capacity import capacity_plan
from generator.chunked import ChunkConfig, TABLES, digest_file, encoded, generate_chunked
from generator.chunk_quality import inspect_chunked, originals, table_rows
from generator.config import Config
from generator.simulation import simulate
from generator.storage import write_json


def reseal(root: Path):
    """Recompute file checksums after corruption so semantic validation must catch the actual defect."""
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    items=[json.loads(line) for line in (root/'files.jsonl').read_text(encoding='utf-8').splitlines()]
    hashes={name:sha256() for name in TABLES}
    summary={name:{'rows':0,'bytes':0,'parts':0} for name in TABLES}
    for item in items:
        data=(root/item['path']).read_bytes()
        item.update(rows=data.count(b'\n'),bytes=len(data),sha256=sha256(data).hexdigest())
        table=item['table']
        hashes[table].update(data)
        for name in ('rows','bytes'):
            summary[table][name]+=item[name]
        summary[table]['parts']+=1
    for name in TABLES:
        summary[name]['logical_sha256']=hashes[name].hexdigest()
    (root/'files.jsonl').write_bytes(b''.join(encoded(row) for row in items))
    manifest['tables']=summary
    manifest['payload_bytes']=sum(v['bytes'] for v in summary.values())
    manifest['offline_payload_bytes']=sum(v['bytes'] for name,v in summary.items() if name!='stream')
    manifest['catalog'].update(bytes=(root/'files.jsonl').stat().st_size,sha256=digest_file(root/'files.jsonl'))
    write_json(root/'manifest.json',manifest)
    write_json(root/'_GENERATION_SUCCESS.json',{'manifest_sha256':digest_file(root/'manifest.json')})


class ChunkedGeneratorTests(unittest.TestCase):
    """Use tiny known grids and adversarial sources before trying a workload above the legacy cap."""

    def setUp(self):
        """Choose 120 readings with global counts that expose incorrect per-chunk rounding."""
        self.config=ChunkConfig(machine_count=2,duration_hours=1,offline_duplicate_rate=0.019,
                                stream_duplicate_rate=0.05,late_rate=0.1)

    def test_legacy_guard_stays_and_chunked_config_accepts_larger_horizon(self):
        """The new writer must not silently remove protection from the old materializing command."""
        with self.assertRaisesRegex(ValueError,'100,000'):
            Config(machine_count=40)
        self.assertEqual(ChunkConfig(machine_count=40).unique_rows,115200)
        with self.assertRaisesRegex(ValueError,'10,000'):
            ChunkConfig(machine_count=10001)

    def test_pre_refactor_physical_hashes_remain_unchanged(self):
        """Two hashes captured before extraction protect RNG ordering, wear, stop events and sensors."""
        fixtures=[(Config(machine_count=1,duration_hours=1),'3ede484d1d3a6121febdc61b0e261fefe3a966442a6d98a026ca2af0165cfbb5'),
                  (Config(machine_count=2,duration_hours=4,wear_per_hour=2,maintenance_every_hours=1),'8c7ccea413611f8e8171edee8d9fcfd93b44d1f9920f10a499ad7f8dedebabbb')]
        for config,expected in fixtures:
            self.assertEqual(sha256(json.dumps(asdict(simulate(config)),sort_keys=True).encode()).hexdigest(),expected)

    def test_exact_global_rates_with_single_row_parts(self):
        """N=120 gives two offline extras, six retries and twelve late readings even with chunk_rows=1."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            manifest=generate_chunked(self.config,root,chunk_rows=1)
            report=inspect_chunked(root)
            self.assertTrue(report['passed'])
            self.assertEqual((report['unique_telemetry_rows'],report['offline_duplicate_extras'],report['late_unique_events']), (120,2,12))
            self.assertEqual(report['stream']['deliveries'],126)
            self.assertEqual(report['stream']['duplicate_extras'],6)
            self.assertTrue(all(v['rows']==v['parts'] for v in manifest['tables'].values()))

    def test_decimal_rate_does_not_lose_an_event_at_an_integer_boundary(self):
        """180 readings at 70% means 126 selected events, despite binary float multiplication."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            config=replace(self.config,machine_count=1,duration_hours=3,late_rate=0.7,
                           offline_duplicate_rate=0.7,stream_duplicate_rate=0.7)
            generate_chunked(config,root,chunk_rows=11)
            report=inspect_chunked(root)
            self.assertEqual(report['offline_duplicate_extras'],126)
            self.assertEqual(report['late_unique_events'],126)
            self.assertEqual(report['stream']['duplicate_extras'],126)

    def test_storage_chunk_size_does_not_change_logical_history(self):
        """Changing file rotation cannot reset simulation, redraw anomalies or reorder stream deliveries."""
        with tempfile.TemporaryDirectory() as directory:
            left=generate_chunked(self.config,Path(directory)/'a',chunk_rows=7)
            right=generate_chunked(self.config,Path(directory)/'b',chunk_rows=53)
            self.assertEqual({k:v['logical_sha256'] for k,v in left['tables'].items()},
                             {k:v['logical_sha256'] for k,v in right['tables'].items()})
            self.assertTrue(inspect_chunked(Path(directory)/'b')['passed'])

    def test_repeat_is_byte_identical_including_manifests(self):
        """Source metadata must not depend on path, host clock or free disk space."""
        with tempfile.TemporaryDirectory() as directory:
            left,right=Path(directory)/'a',Path(directory)/'b'
            for root in (left,right):
                generate_chunked(self.config,root,chunk_rows=17)
            self.assertEqual({p.relative_to(left):p.read_bytes() for p in left.rglob('*') if p.is_file()},
                             {p.relative_to(right):p.read_bytes() for p in right.rglob('*') if p.is_file()})

    def test_wear_failure_and_maintenance_continue_across_parts(self):
        """Compare a continuous four-hour history to the pre-existing physical simulator, including stops."""
        config=replace(self.config,duration_hours=4,wear_per_hour=2,maintenance_every_hours=1)
        expected=simulate(config)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            manifest=generate_chunked(config,root,chunk_rows=11)
            rows=list(originals(root,manifest))
            physical=lambda r:{k:v for k,v in r.items() if k not in ('received_ts','created_ts')}
            self.assertEqual([physical(r) for r in rows],[physical(r) for r in expected.telemetry])
            for table,source in [('failures',expected.failures),('maintenance',expected.maintenance)]:
                self.assertEqual(sorted(table_rows(root,manifest,table),key=lambda r:json.dumps(r,sort_keys=True)),
                                 sorted(source,key=lambda r:json.dumps(r,sort_keys=True)))
            self.assertTrue(inspect_chunked(root)['passed'])
            self.assertGreater(len(expected.failures),0)

    def test_rate_and_firmware_boundaries(self):
        """Zero/full anomaly rates and all-v1/all-v2 sources remain valid across empty partitions."""
        for rate in (0,1):
            for fraction in (0,1):
                with self.subTest(rate=rate,fraction=fraction), tempfile.TemporaryDirectory() as directory:
                    config=replace(self.config,offline_duplicate_rate=rate,stream_duplicate_rate=rate,late_rate=rate,schema_v2_fraction=fraction)
                    root=Path(directory)/'source'
                    generate_chunked(config,root,chunk_rows=13)
                    report=inspect_chunked(root)
                    self.assertEqual(report['offline_duplicate_extras'],120*rate)
                    self.assertEqual(report['late_unique_events'],120*rate)
                    self.assertEqual(report['stream']['duplicate_extras'],120*rate)
                    self.assertEqual(report['schema_unique_counts'],{'1' if fraction==1 else '2':120})

    def test_offline_only_and_capacity_plan_do_not_claim_generated_100gb(self):
        """A plan uses measured bytes but never creates the target dataset or fabricates stream costs."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            generate_chunked(self.config,root,include_stream=False)
            report=inspect_chunked(root)
            plan=capacity_plan(report,root,100,offline_only=True)
            self.assertEqual(plan['target_offline_bytes'],100_000_000_000)
            self.assertEqual(plan['kind'],'estimate_only')
            self.assertFalse(plan['generation_authorized_by_plan'])
            self.assertEqual(plan['estimated_required_free_bytes'],sum(plan['assumed_budget_bytes'].values()))
            self.assertFalse(report['stream']['generated'])
            with self.assertRaisesRegex(ValueError,'lacks stream'):
                capacity_plan(report,root)

    def test_overwrite_and_incomplete_run_guards(self):
        """An existing run stays intact; hitting a hard byte cap cannot create a completion marker."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            generate_chunked(self.config,root)
            before=digest_file(root/'manifest.json')
            with self.assertRaises(FileExistsError):
                generate_chunked(self.config,root)
            self.assertEqual(digest_file(root/'manifest.json'),before)
            failed=Path(directory)/'incomplete'
            with self.assertRaisesRegex(OSError,'maximum output bytes'):
                generate_chunked(self.config,failed,maximum_bytes=1000)
            self.assertFalse((failed/'_GENERATION_SUCCESS.json').exists())
            with self.assertRaises(OSError):
                inspect_chunked(failed)

    def test_arrival_memory_guard_rejects_shape_before_writing(self):
        """An unsafe lateness buffer is rejected without creating a partial dataset."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            with self.assertRaisesRegex(ValueError,'arrival buffer'):
                generate_chunked(self.config,root,max_buffered_deliveries=1)
            self.assertFalse(root.exists())

    def test_checksum_and_unlisted_file_corruption_are_rejected(self):
        """Integrity checks include manifest binding and every file in the source directory."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            generate_chunked(self.config,root)
            (root/'unexpected.jsonl').write_bytes(b'{}\n')
            with self.assertRaisesRegex(ValueError,'unlisted'):
                inspect_chunked(root)
            (root/'unexpected.jsonl').unlink()
            part=next((root/'telemetry').rglob('*.jsonl'))
            part.write_bytes(part.read_bytes()+b' \n')
            with self.assertRaisesRegex(ValueError,'integrity'):
                inspect_chunked(root)

    def test_resealed_missing_reading_still_fails_grid(self):
        """Valid checksums cannot disguise a hole in the machine/time grid."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            generate_chunked(self.config,root,chunk_rows=1000)
            path=root/'telemetry/schema_v1/part-000000.jsonl'
            lines=path.read_bytes().splitlines(keepends=True)
            path.write_bytes(b''.join(lines[1:]))
            reseal(root)
            with self.assertRaisesRegex(ValueError,'grid'):
                inspect_chunked(root)

    def test_resealed_conflicting_duplicate_across_parts_is_rejected(self):
        """An adjacent duplicate may cross file boundaries but cannot alter its measurement."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            generate_chunked(replace(self.config,offline_duplicate_rate=1),root,chunk_rows=1)
            path=root/'telemetry/schema_v1/part-000001.jsonl'
            row=json.loads(path.read_bytes())
            row['temperature_c']+=1
            path.write_bytes(encoded(row))
            reseal(root)
            with self.assertRaisesRegex(ValueError,'duplicate payload'):
                inspect_chunked(root)

    def test_resealed_stream_payload_must_match_offline(self):
        """A delivery with valid storage checksums still needs the original sensor payload."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            generate_chunked(self.config,root)
            path=root/'stream/part-000000.jsonl'
            rows=[json.loads(line) for line in path.read_bytes().splitlines()]
            rows[0]['event']['rpm']+=1
            path.write_bytes(b''.join(encoded(r) for r in rows))
            reseal(root)
            with self.assertRaisesRegex(ValueError,'stream payload'):
                inspect_chunked(root)

    def test_cli_protects_source_and_returns_failure_for_invalid_inspect(self):
        """Public commands report failures without overwriting an immutable source file."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            generate_chunked(self.config,root)
            args=[sys.executable,'-m','generator.chunked','inspect','--source',str(root),'--report',str(root/'manifest.json')]
            result=subprocess.run(args,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('outside',result.stderr)
            self.assertTrue(inspect_chunked(root)['passed'])

    def test_zero_retries_do_not_retain_events_for_an_unused_retry_delay(self):
        """A disabled retry fault cannot turn a bounded join into a whole-run ID map."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            generate_chunked(replace(self.config,stream_duplicate_rate=0,retry_delay_seconds=10**8),root)
            report=inspect_chunked(root)
            self.assertEqual(report['stream']['duplicate_extras'],0)
            self.assertLess(report['stream']['peak_join_events'],120)

    def test_resealed_missing_failure_truth_is_rejected(self):
        """A failed motor requires a matching causal failure interval even if all hashes are valid."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            generate_chunked(replace(self.config,duration_hours=4,wear_per_hour=2),root)
            path=root/'failure_events/part-000000.jsonl'
            lines=path.read_bytes().splitlines(keepends=True)
            self.assertGreater(len(lines),1)
            path.write_bytes(b''.join(lines[1:]))
            reseal(root)
            with self.assertRaisesRegex(ValueError,'status disagrees'):
                inspect_chunked(root)

    def test_cli_distinguishes_byte_repeat_from_different_chunk_layout(self):
        """Different chunks may preserve logical data while failing a strict byte-repeat check."""
        with tempfile.TemporaryDirectory() as directory:
            left,right=Path(directory)/'left',Path(directory)/'right'
            generate_chunked(self.config,left,chunk_rows=13)
            generate_chunked(self.config,right,chunk_rows=29)
            report=Path(directory)/'comparison.json'
            command=[sys.executable,'-m','generator.chunked','compare','--left',str(left),'--right',str(right),'--report',str(report)]
            self.assertEqual(subprocess.run(command+['--logical-only'],capture_output=True).returncode,0)
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)
            result=json.loads(report.read_text(encoding='utf-8'))
            self.assertTrue(result['logical_tables_identical'])
            self.assertFalse(result['all_source_bytes_identical'])


if __name__=='__main__':
    unittest.main()
