"""Verify the focused Docker/Flink/schema review from original bundled outputs without a live VM or large dataset."""
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import argparse
import json
import math
import sqlite3
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'docs/evidence/data-platform-review'


def load(path):
    """Decode one explicitly named structured evidence file."""
    return json.loads(path.read_bytes())


def archive(path):
    """Read small regular payloads without extracting paths or following archive links."""
    result = {}
    with tarfile.open(path) as bundle:
        for member in bundle.getmembers():
            name = PurePosixPath(member.name)
            assert not name.is_absolute() and '..' not in name.parts and (member.isfile() or member.isdir())
            if member.isfile():
                assert member.size < 2_000_000 and member.name not in result
                result[member.name] = bundle.extractfile(member).read()
                assert sum(map(len, result.values())) < 10_000_000
    return result


class Tables(HTMLParser):
    """Read the actual pandas HTML output cells rather than trusting a report's pass flag."""

    def __init__(self):
        """Initialize one output-table parser."""
        super().__init__(); self.rows = []; self.row = []; self.cell = None

    def handle_starttag(self, tag, attrs):
        """Collect only header/data cells in each result row."""
        if tag == 'tr': self.row = []
        if tag in ['th', 'td']: self.cell = ''

    def handle_data(self, data):
        """Preserve displayed scalar values, including decoded arrow characters."""
        if self.cell is not None: self.cell += data

    def handle_endtag(self, tag):
        """Finish a cell or nonempty table row."""
        if tag in ['th', 'td'] and self.cell is not None:
            self.row.append(self.cell.strip()); self.cell = None
        if tag == 'tr' and self.row: self.rows.append(self.row)


def check():
    """Authenticate the payload inventory and check predictions, retained stream stores and executed SQL results."""
    manifest = load(EVIDENCE / 'manifest.json')
    expected = {item['path'] for item in manifest['files']}
    actual = {p.relative_to(EVIDENCE).as_posix() for p in EVIDENCE.rglob('*') if p.is_file()} - {'manifest.json'}
    assert actual == expected
    for item in manifest['files']:
        body = (EVIDENCE / item['path']).read_bytes()
        assert len(body) == item['bytes'] and sha256(body).hexdigest() == item['sha256'], item['path']

    images = archive(EVIDENCE / 'docker/native.tar.gz')
    image = load(EVIDENCE / 'docker/verification.json')
    assert json.loads(images['image-b/verification.json']) == image and image['passed']
    before, after = [image['images'][variant]['bytes'] for variant in ['baseline', 'optimized']]
    assert before > after and image['saved_bytes'] == before - after
    assert math.isclose(image['reduction_percent'], 100 * (before - after) / before, abs_tol=1e-12)
    assert image['images']['baseline']['python_version'] == image['images']['optimized']['python_version']
    assert images['image-b/probe-baseline.txt'] == images['image-b/probe-optimized.txt']
    golden = json.loads(images['image-b/context/tests/fixtures/predictor-native-spark.json'])
    assert golden == load(ROOT / 'tests/fixtures/predictor-native-spark.json')
    assert golden['portable_sha256'] == sha256(images['image-b/context/models/pump-failure-v1/portable.json']).hexdigest()
    response = json.loads(images['image-b/probe-optimized.txt'])
    assert response['ready']['ready'] and response['live']['live']
    predictions = response['result']['predictions']
    assert len(predictions) == len(golden['samples']) == 32
    for row, truth in zip(predictions, golden['samples']):
        assert abs(row['risk_score'] - truth['expected_risk_score']) < 1e-12
        assert row['predicted_failure'] == truth['expected_predicted_failure']
    assert b'read-only' in images['image-a/copy-probes-baseline.txt']

    native = archive(EVIDENCE / 'stream/native.tar.gz')
    stream = load(EVIDENCE / 'stream/verification.json')
    assert json.loads(native['outputs/mentor-native-20261101/stream-recovered-c.json']) == stream and stream['passed']
    pair = [('baseline', [(6, 100), (2, 80), (2, 200)]), ('corrected', [(3, 60), (1, 40), (1, 100)])]
    for variant, values in pair:
        result = stream[variant]
        assert result['runtime']['passed'] and result['runtime']['records'] == result['kafka_end_offset'] == 22
        assert len(result['windows']) == 3
        assert [(r['machine_id'], r['window_start']) for r in result['windows']] == [
            ('P-001', '2026-10-01T00:00:00Z'), ('P-001', '2026-10-01T00:10:00Z'), ('P-002', '2026-10-01T00:00:00Z')]
        for window, (count, total) in zip(result['windows'], values):
            assert window['reading_count'] == count and window['temperature_sum'] == total
            assert math.isclose(window['temperature_mean'], total / count)
        temporary_root = ROOT / 'outputs'
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='data-review-check-', dir=temporary_root) as directory:
            assert Path(directory).resolve().is_relative_to(temporary_root.resolve())
            snapshot = Path(directory) / (variant + '.sqlite')
            snapshot.write_bytes(native['data/stream/mentor-stream-20261101b/' + variant + '.sqlite'])
            # Exported snapshots are complete backups; immutable readback never opens or rewrites a WAL sidecar.
            database = sqlite3.connect(snapshot.as_uri() + '?mode=ro&immutable=1', uri=True)
            try:
                stored = [json.loads(row[0]) for row in database.execute('SELECT payload FROM windows ORDER BY machine_id, window_start')]
                assert stored == result['windows']
            finally: database.close()
    assert stream['baseline']['runtime']['job_id'] != stream['corrected']['runtime']['job_id']
    assert [stream[v]['runtime']['watermark_delay_minutes'] for v in ['baseline', 'corrected']] == [0, 15]
    assert stream['corrected']['audit_counts']['duplicate'] == 13 and stream['corrected']['durable_ledger_records'] == 6
    assert 'D' in stream['baseline']['late_event_ids'] and stream['corrected']['late_event_ids'] == ['E']
    retention = stream['retention_probe']
    assert retention['windows'][0]['reading_count'] == 1 and retention['audit_counts']['expired'] >= 1
    assert retention['duplicates_from_durable_ledger'] == 1
    assert b'Failed to create directory for shared state' in native['outputs/mentor-stream-20261101a/baseline-console.txt']
    assert b'git' in native['outputs/mentor-native-20261101/stream-controller-b.log']

    entrypoint = archive(EVIDENCE / 'stream-entrypoint/native.tar.gz')
    fresh = load(EVIDENCE / 'stream-entrypoint/verification.json')
    prefix = 'outputs/mentor-stream-20261101d/'
    assert json.loads(entrypoint[prefix + 'verification.json']) == fresh
    assert fresh['passed'] and fresh['source_unchanged']
    source_manifest = load(EVIDENCE / 'stream-entrypoint/source-manifest.json')
    assert json.loads(entrypoint[prefix + 'source-manifest.json']) == fresh['source'] == source_manifest
    assert len(source_manifest['files']) == 11
    for row in source_manifest['files']:
        assert sha256(entrypoint[row['path']]).hexdigest() == row['sha256']
        # These are UTF-8 source files; Git may check them out with CRLF on Windows.
        current = (ROOT / row['path']).read_bytes().replace(b'\r\n', b'\n')
        assert sha256(current).hexdigest() == row['sha256'], row['path']
    finalization = load(EVIDENCE / 'stream-entrypoint/finalization.json')
    assert finalization['passed'] and finalization['new_review_broker_stopped'] and finalization['credential_intermediate_absent']
    assert finalization['core_runner_sha256'] == '7f959636c6ef74c4330a5ca8a9f480696762ecf34ecb506eef69afc947c6f3ae'
    assert finalization['entrypoint_sha256'] == sha256(entrypoint['scripts/run-stream-review.py']).hexdigest()
    for variant, values in pair:
        result = fresh[variant]
        assert result['runtime']['passed'] and result['runtime']['records'] == result['kafka_end_offset'] == 22
        assert [(w['reading_count'], w['temperature_sum']) for w in result['windows']] == values
        with tempfile.TemporaryDirectory(prefix='data-review-fresh-', dir=ROOT / 'outputs') as directory:
            snapshot = Path(directory) / (variant + '.sqlite')
            snapshot.write_bytes(entrypoint['data/stream/mentor-stream-20261101d/' + variant + '.sqlite'])
            database = sqlite3.connect(snapshot.as_uri() + '?mode=ro&immutable=1', uri=True)
            try:
                assert [json.loads(r[0]) for r in database.execute('SELECT payload FROM windows ORDER BY machine_id, window_start')] == result['windows']
            finally: database.close()
    assert fresh['corrected']['audit_counts']['duplicate'] == 13 and fresh['corrected']['late_event_ids'] == ['E']
    assert 'D' in fresh['baseline']['late_event_ids']
    assert fresh['retention_probe']['audit_counts']['expired'] == 2
    assert fresh['retention_probe']['duplicates_from_durable_ledger'] == 1
    assert fresh['retention_probe']['windows'][0]['reading_count'] == 1

    notebook = load(EVIDENCE / 'schema/executed.ipynb')
    source = load(ROOT / 'notebooks/04_data_model_inspection.ipynb')
    assert [c['source'] for c in notebook['cells']] == [c['source'] for c in source['cells']]
    tables, stdout = {}, ''
    for cell in notebook['cells']:
        if cell['cell_type'] != 'code': continue
        assert cell['execution_count'] is not None
        for output in cell.get('outputs', []):
            assert output['output_type'] != 'error'
            stdout += ''.join(output.get('text', []))
            html = output.get('data', {}).get('text/html')
            if html:
                parser = Tables(); parser.feed(''.join(html))
                tables[tuple(parser.rows[0][1:])] = [row[1:] for row in parser.rows[1:]]
    assert tables[('stage', 'rows', 'unique_events')] == [['Bronze telemetry exports', '58752', '57600'], ['Silver normalized readings', '57600', '57600']]
    assert tables[('versions', 'distinct_version_keys', 'machines', 'current_versions')] == [['21', '21', '20', '20']]
    assert tables[('schema_version', 'unique_readings', 'pressure_present', 'pressure_unknown', 'normalized_type')] == [
        ['1', '28800', '0', '28800', 'DOUBLE'], ['2', '28800', '28800', '0', 'DOUBLE']]
    assert tables[('snapshots', 'unique_snapshots', 'future_input_rows')] == [['11520', '11520', '0']]
    relations = tables[('relationship', 'fact_rows', 'missing_parent')]
    assert [row[1:] for row in relations] == [['57600', '0'], ['31', '0'], ['39', '0'], ['21', '0']]
    assert 'PASS: all original Parquet bytes unchanged' in stdout
    pins = load(EVIDENCE / 'schema/input-files.json')['files']
    assert len(pins) == 31 and sum(row['bytes'] for row in pins) == 9_612_529
    assert load(EVIDENCE / 'schema/vm-source-recheck.json')['source_unchanged']
    for row in load(EVIDENCE / 'spark/queries.json')['queries']:
        assert sha256((EVIDENCE / 'spark' / ('query-' + str(row['id']) + '.txt')).read_bytes()).hexdigest() == row['physical_plan_sha256']
    capture = load(EVIDENCE / 'datahub/capture.json')
    assert (capture['dp1']['inputs'], capture['dp1']['outputs']) == (4, 4)
    assert (capture['dp2']['inputs'], capture['dp2']['outputs']) == (4, 8)
    for stage in ['dp1', 'dp2']:
        dom = (EVIDENCE / 'datahub' / ('datahub-' + stage + '-dom.txt')).read_text()
        pipeline_id = capture[stage]['task'].split('dataFlow:(airflow,', 1)[1].split(',', 1)[0]
        assert ('Home Platform Task ' + pipeline_id + ' ingest') in dom
        assert all(name in dom for name in capture[stage]['output_tables'])
    quality = (EVIDENCE / 'datahub/datahub-quality-dom.txt').read_text()
    assert capture['quality']['passing'] == 7
    assert all(name in quality for name in capture['quality']['names'])
    assert 'jenkins-2-1' in quality and '10/27/2026' in quality and 'Passing' in quality
    return {'passed': True, 'payloads': len(expected), 'http_predictions_per_image': 32, 'stream_pair_records_each': 22,
            'schema_input_files': 31, 'manifest_bound_runtime_sources': 11,
            'scope': 'Recorded focused evidence only; no new live services, full-rubric grade or independent schema/dedup timing claim.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = check()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
