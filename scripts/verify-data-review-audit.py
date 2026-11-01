"""Authenticate the eleven-topic review and inspect saved SQL outputs against original native receipts."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
import argparse
import json
import runpy
import sqlite3
import tarfile
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CAPSULE = ROOT / 'docs/evidence/data-review-audit'


def load(path):
    """Decode an explicitly named JSON artifact."""
    return json.loads(path.read_bytes())


def notebook_tables(book):
    """Parse actual displayed HTML cells and reject missing execution or error outputs."""
    parser_type = runpy.run_path(str(ROOT / 'scripts/verify-data-platform-review.py'))['Tables']
    tables, svgs, stdout = {}, [], ''
    code = [c for c in book['cells'] if c['cell_type'] == 'code']
    assert [c['execution_count'] for c in code] == list(range(1, 8))
    for cell in code:
        for output in cell.get('outputs', []):
            assert output['output_type'] != 'error'
            stdout += ''.join(output.get('text', []))
            data = output.get('data', {})
            if 'text/html' in data:
                parser = parser_type(); parser.feed(''.join(data['text/html']))
                header = tuple(parser.rows[0][1:])
                assert header not in tables
                tables[header] = [r[1:] for r in parser.rows[1:]]
            if 'image/svg+xml' in data: svgs.append(''.join(data['image/svg+xml']))
    assert len(tables) == 8 and len(svgs) == 1
    assert 'PASS: all original archive/report/Parquet bytes unchanged' in stdout
    return tables, svgs[0]


def check():
    """Check coverage pins, independently queried SQLite results and table/graph outputs."""
    manifest = load(CAPSULE / 'manifest.json')
    expected = {r['path'] for r in manifest['files']}
    actual = {p.relative_to(CAPSULE).as_posix() for p in CAPSULE.rglob('*') if p.is_file()} - {'manifest.json'}
    assert actual == expected
    for row in manifest['files']:
        path = PurePosixPath(row['path'])
        assert not path.is_absolute() and '..' not in path.parts
        body = (CAPSULE / row['path']).read_bytes()
        assert len(body) == row['bytes'] and sha256(body).hexdigest() == row['sha256']
    coverage = load(CAPSULE / 'coverage.json')
    assert [r['id'] for r in coverage['topics']] == [f'R{i:02}' for i in range(1, 12)]
    for row in coverage['topics']:
        assert row['code'] and row['documentation'] and row['visual_evidence'] and row['limits']
        assert row['status'] == 'addressed_at_recorded_scope'
    bound = {row['path'] for row in coverage['files']}
    for topic in coverage['topics']:
        assert set(topic['code'] + topic['documentation'] + topic['visual_evidence']) <= bound
    for row in coverage['files']:
        path = (ROOT / row['path']).resolve()
        assert path.is_relative_to(ROOT) and path.is_file(), row['path']
        body = path.read_bytes()
        if row['mode'] == 'lf_text': body = body.replace(b'\r\n', b'\n')
        else: assert row['mode'] == 'exact_bytes'
        assert sha256(body).hexdigest() == row['sha256'], row['path']

    book = load(CAPSULE / 'executed.ipynb')
    source = load(ROOT / 'notebooks/05_data_review.ipynb')
    assert [(c['cell_type'], c['source']) for c in book['cells']] == [(c['cell_type'], c['source']) for c in source['cells']]
    tables, svg = notebook_tables(book)
    original = ROOT / 'docs/evidence/data-platform-review'
    report = load(original / 'stream-entrypoint/verification.json')
    windows, late, barriers, audits, identities = [], [], [], [], []
    with tarfile.open(original / 'stream-entrypoint/native.tar.gz') as archive, tempfile.TemporaryDirectory(prefix='review-check-', dir=ROOT / 'outputs') as folder:
        for variant in ['baseline', 'corrected', 'retention-probe']:
            member = archive.getmember(f'data/stream/mentor-stream-20261101d/{variant}.sqlite')
            assert member.isfile() and member.size < 1_000_000
            path = Path(folder) / (variant + '.sqlite')
            path.write_bytes(archive.extractfile(member).read())
            before = sha256(path.read_bytes()).hexdigest()
            result = report[variant.replace('-', '_')]
            identities.append([variant, result['runtime']['job_id'], str(result['runtime']['records']), str(result['runtime']['watermark_delay_minutes'])])
            db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
            try:
                rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM windows ORDER BY machine_id,window_start')]
                assert rows == result['windows']
                counts = dict(db.execute('SELECT kind,count(*) FROM audits GROUP BY kind'))
                audits.append([variant, *[str(counts.get(k, 0)) for k in ['unique', 'duplicate', 'expired']], str(result['duplicates_from_durable_ledger']), str(sum(r['reading_count'] for r in rows))])
                if variant == 'retention-probe': continue
                windows.extend([[variant, r['machine_id'], r['window_start'][11:16], r['event_timestamp'][11:16], str(r['reading_count']), str(float(r['temperature_sum'])), f"{r['temperature_mean']:.6f}"] for r in rows])
                ids = [r[0] for r in db.execute("SELECT json_extract(payload,'$.event.event_id') FROM audits WHERE kind='late' ORDER BY identity")]
                late.append([variant, ', '.join(ids), str('D' in ids), str('E' in ids)])
                for payload, in db.execute("SELECT payload FROM audits WHERE kind='progress' ORDER BY identity"):
                    r = json.loads(payload)
                    watermark = datetime.fromtimestamp(r['watermark_ms'] / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                    barriers.append([variant, str(r['marker']), r['event_ts'][11:19], watermark])
            finally:
                db.close()
                assert sha256(path.read_bytes()).hexdigest() == before
    assert tables[('variant', 'job_id', 'records', 'watermark_delay_min')] == identities
    assert tables[('variant', 'machine', 'window_start_utc', 'window_end_utc', 'readings', 'temperature_sum', 'temperature_mean')] == windows
    assert tables[('variant', 'distinct_late_event_ids', 'D_quarantined', 'E_quarantined')] == late
    assert tables[('variant', 'barrier', 'event_time_utc', 'observed_watermark_utc')] == barriers
    assert tables[('variant', 'unique_ID_audits', 'duplicate_delivery_audits', 'cache_expirations', 'ledger_duplicates', 'final_total_readings')] == audits

    batch = load(ROOT / 'docs/evidence/day2-final/batch-report.json')
    counts = batch['counts']
    mapping = {'raw_department_records': 'bronze_records', 'stg_failure': 'failures', 'stg_machine_snapshots': 'dim_machine_versions',
        'stg_maintenance': 'maintenance', 'stg_telemetry': 'silver_telemetry', 'dim_factory': 'factories',
        'dim_machine': 'dim_machine_versions', 'fact_failure': 'failures', 'fact_maintenance': 'maintenance',
        'fact_telemetry': 'silver_telemetry', 'feat_machine_10m': 'feature_rows', 'label_failure_1h': 'feature_rows', 'training_failure_1h': 'training_rows'}
    inventory = tables[('zone', 'table', 'rows', 'columns')]
    assert len(inventory) == len(mapping) == 13 and len({(r[0], r[1]) for r in inventory}) == 13
    catalog = {(r['schema'].removeprefix('day2_'), r['name']): r for r in load(original / 'schema/vm-inventory.json')['tables'] if r['schema'].startswith('day2_')}
    assert len(catalog) == 13
    for zone, table, rows, columns in inventory:
        assert int(rows) == counts[mapping[table]]
        assert (int(rows), int(columns)) == (catalog[(zone, table)]['rows'], len(catalog[(zone, table)]['columns']))
        if zone + '.' + table in batch['schemas']:
            assert int(columns) == len(batch['schemas'][zone + '.' + table]['fields'])
    relations = tables[('child', 'parent', 'key', 'child_rows', 'missing_parent', 'null_key')]
    assert relations == [['fact_telemetry', 'dim_machine', 'machine_sk', '57600', '0', '0'],
        ['fact_failure', 'dim_machine', 'machine_sk', '31', '0', '0'], ['fact_maintenance', 'dim_machine', 'machine_sk', '39', '0', '0'],
        ['dim_machine', 'dim_factory', 'factory_id', '21', '0', '0']]
    links = tables[('source', 'destination', 'key', 'rows', 'unmatched_keys')]
    assert len(links) == 6 and [r[-2:] for r in links] == [['57600', '0'], ['31', '0'], ['39', '0'], ['11520', '0'], ['11520', '0'], ['9910', '0']]
    diagram = ET.fromstring(svg)
    ns = {'s': 'http://www.w3.org/2000/svg'}
    paths = diagram.findall('s:path', ns)
    assert len(paths) == 4 and all(p.get('marker-end') == 'url(#join-end)' for p in paths)
    labels = [t.text for t in diagram.findall('s:text', ns)]
    for table in ['fact_telemetry', 'fact_failure', 'fact_maintenance', 'dim_machine', 'dim_factory']:
        offset = labels.index(table)
        key = 'factory_id' if table == 'dim_factory' else 'machine_sk'
        assert labels[offset + 1:offset + 3] == [f'{counts[mapping[table]]:,} rows', key + (' [unique]' if table.startswith('dim_') else ' [logical FK]')]
    assert not diagram.findall('.//s:script', ns) and not diagram.findall('.//s:image', ns)
    captures = load(CAPSULE / 'capture.json')
    assert captures['source_run'] == 'mentor-stream-20261101d' and captures['native_flink_rerun'] is False
    assert len(captures['captures']) == 6
    assert {r['file'] for r in captures['captures']} == {p.name for p in (CAPSULE / 'screenshots').glob('*.jpg')}
    return {'passed': True, 'review_topics': 11, 'bound_files': len(bound), 'executed_code_cells': 7,
        'independent_sqlite_snapshots': 3, 'displayed_tables_checked': 8, 'persisted_tables': 13,
        'dim_fact_links': 4, 'cross_table_key_checks': 6, 'native_captures': 6,
        'scope': 'Recorded review coverage and independently inspected final SQLite outputs. Saved Parquet query outputs match original batch receipts; no live Flink rerun, fresh Parquet scan, mentor approval or rubric score is inferred by this offline verifier.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output:
        args.output = args.output.resolve()
        if not args.output.is_relative_to(ROOT / 'outputs') or args.output.exists(): raise ValueError('Use a fresh receipt under outputs/')
    result = check()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
