"""Recalculate the sealed native API metrics, trace graph, regression gate and rollout evidence offline."""
from git_sources import historical_blob
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import importlib.util
import json
import math
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'docs/evidence/api-observability-linux'


def read(name):
    """Read a retained JSON response after checking its original bytes."""
    return json.loads((BASE / name).read_bytes())


def manifest(folder):
    """Reject missing, extra, unsafe or changed payloads in a capsule."""
    directory = BASE / folder
    seal = json.loads((directory / 'manifest.json').read_bytes())
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    assert actual == set(seal['files']) | {'manifest.json'}
    for name, entry in seal['files'].items():
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        raw = (directory / name).read_bytes()
        assert len(raw) == entry['bytes'] and sha256(raw).hexdigest() == entry['sha256'], name
    return len(seal['files'])


def source(name):
    """Bind recorded source hashes to actual Git objects, without trusting the working tree."""
    seal = read(name)
    for path, wanted in seal['files'].items():
        raw = historical_blob(ROOT, seal['commit'] + ':' + path)
        assert sha256(raw).hexdigest() == wanted, path
    return seal


def samples(pattern):
    """Parse SDK expositions independently of the workload's Prometheus parser."""
    rows = []
    for path in sorted((BASE / 'native/workload/run').glob(pattern)):
        for line in path.read_text().splitlines():
            if not line or line.startswith('#'):
                continue
            match = re.fullmatch(r'([\w:]+)(?:\{(.*)\})?\s+(\S+)', line)
            assert match, line
            name, labels, value = match.groups()
            tags = {k: json.loads('"' + v + '"') for k, v in re.findall(r'(\w+)="((?:\\.|[^"\\])*)"', labels or '')}
            rows.append((name, tags, float(value)))
    assert rows
    return rows


def route_key(labels):
    """Normalize the labels used by the actual six dashboard panels."""
    return labels['service_name'], labels.get('http_target', ''), labels.get('http_status_code', '')


def rate(values, end):
    """Compute the classic counter rate with reset and scrape-boundary extrapolation."""
    points = [(float(t), float(v)) for t, v in values]
    assert len(points) >= 2 and all(b[0] > a[0] for a, b in zip(points, points[1:]))
    elapsed = points[-1][0] - points[0][0]
    delta = points[-1][1] - points[0][1]
    delta += sum(a[1] for a, b in zip(points, points[1:]) if b[1] < a[1])
    average = elapsed / (len(points) - 1)
    left, right = points[0][0] - (end - 60), end - points[-1][0]
    if delta > 0 and points[0][1] >= 0:
        left = min(left, elapsed * points[0][1] / delta)
    left = average / 2 if left >= average * 1.1 else left
    right = average / 2 if right >= average * 1.1 else right
    return delta * (elapsed + left + right) / elapsed / 60


def equal(actual, expected):
    """Require identical series sets and finite, close numeric values."""
    assert actual.keys() == expected.keys(), (actual, expected)
    assert all(math.isfinite(actual[k]) and math.isclose(actual[k], v, rel_tol=1e-8, abs_tol=1e-10) for k, v in expected.items())


def metrics_and_ledger():
    """Rebuild the HTTP ledger, SDK deltas and all 28 values from original SDK/matrix samples."""
    counter = {}
    for stage in ['before', 'after']:
        counts = Counter()
        for name, tags, value in samples(stage + '-*.prom'):
            if name == 'http_server_duration_milliseconds_count':
                kind = tags['service_name'].removeprefix('iot-').removesuffix('-api')
                counts[(kind, tags['http_method'], tags['http_status_code'], tags.get('http_target', ''))] += value
        counter[stage] = counts
    ledger = [json.loads(line) for line in (BASE / 'native/workload/run/requests.jsonl').read_text().splitlines()]
    assert len(ledger) == 747 and [r['sequence'] for r in ledger] == list(range(747))
    assert Counter(r['status'] for r in ledger) == {200: 741, 422: 3, 404: 2, 409: 1}
    expected_delta = Counter()
    for row in ledger:
        expected_delta[(row['service'], row['method'], str(row['status']), row['route'])] += 1
        if (row['service'], row['method'], row['status']) == ('feature', 'POST', 200):
            expected_delta[('predictor', 'POST', '200', '/v1/models/{model_name}:predict')] += 1
    delta = {k: counter['after'][k] - counter['before'][k] for k in counter['after'] | counter['before'] if counter['after'][k] != counter['before'][k]}
    assert delta == expected_delta and len(delta) == 10 and sum(delta.values()) == 932
    before = read('native/workload/drift-ready-before.json')
    after = read('native/workload/drift-ready-after.json')
    assert after['stored_windows'] - before['stored_windows'] == 1
    expected = {i: defaultdict(float) for i in range(1, 7)}
    duration_sum, count = defaultdict(float), defaultdict(float)
    for name, tags, value in samples('after-*.prom'):
        service = tags.get('service_name')
        if name == 'http_server_duration_milliseconds_count':
            expected[1][(service, '', '')] += value
            expected[6][route_key(tags)] += value
            count[service] += value
            if tags['http_status_code'].startswith(('4', '5')):
                expected[3][(service, '', tags['http_status_code'])] += value
        elif name == 'http_server_duration_milliseconds_sum':
            duration_sum[service] += value
    assert dict(count) == {'iot-drift-api': 186, 'iot-feature-api': 774, 'iot-predictor-api': 567}
    for service in count:
        expected[5][(service, '', '')] = duration_sum[service] / count[service] / 1000
    stamp = read('native/metrics-readback/verification.json')['evaluation_epoch']
    assert stamp == int(datetime.fromisoformat(read('native/workload/run/verification.json')['checked_utc']).timestamp()) + 6
    for series in read('native/metrics-readback/raw-count-matrix.json')['data']['result']:
        expected[2][(series['metric']['service_name'], '', '')] += rate(series['values'], stamp)
    buckets = defaultdict(lambda: defaultdict(float))
    for series in read('native/metrics-readback/raw-bucket-matrix.json')['data']['result']:
        buckets[series['metric']['service_name']][float(series['metric']['le'])] += rate(series['values'], stamp)
    for service, table in buckets.items():
        ordered = sorted(table.items())
        assert math.isinf(ordered[-1][0]) and ordered[-1][1] > 0
        rank, lower, previous = .95 * ordered[-1][1], 0, 0
        for upper, value in ordered:
            if value >= rank:
                quantile = lower if math.isinf(upper) else upper if upper <= 0 else lower + (upper - lower) * (rank - previous) / (value - previous)
                break
            lower, previous = upper, value
        expected[4][(service, '', '')] = quantile / 1000
    records = read('native/metrics-readback/panel-readbacks.json')
    panels = {p['id']: p for p in read('native/metrics-readback/dashboard.json')['dashboard']['panels']}
    assert len(records) == len(panels) == 6
    for row in records:
        number = row['panel']
        assert row['expr'] == panels[number]['targets'][0]['expr']
        series = row['prometheus']['data']['result']
        actual = {route_key(r['metric']): float(r['value'][1]) for r in series}
        assert len(actual) == len(series)
        equal(actual, expected[number])
        plotted = {}
        answer = row['grafana']['results']['A']
        assert not answer.get('error') and answer.get('status', 200) == 200
        for frame in answer['frames']:
            for field, values in zip(frame['schema']['fields'], frame['data']['values'], strict=True):
                if field['type'] == 'number' and 'service_name' in field.get('labels', {}):
                    assert len(values) == 1 and route_key(field['labels']) not in plotted
                    plotted[route_key(field['labels'])] = float(values[0])
        equal(plotted, expected[number])
    assert sum(map(len, expected.values())) == 28


def traces():
    """Check actual OTLP resource/attribute allowlists and three connected spans per trace."""
    groups = defaultdict(list)
    raw = (BASE / 'native/trace-readback/searched-traces.json').read_bytes()
    for resource in json.loads(raw)['result']['resourceSpans']:
        attrs = {a['key']: a['value']['stringValue'] for a in resource['resource']['attributes']}
        assert set(attrs) == {'service.name', 'deployment.environment.name'}
        assert attrs['deployment.environment.name'] == 'coursework-synthetic-lab'
        for scope in resource['scopeSpans']:
            for span in scope['spans']:
                assert re.fullmatch('[0-9a-f]{32}', span['traceId']) and re.fullmatch('[0-9a-f]{16}', span['spanId'])
                assert int(span['endTimeUnixNano']) > int(span['startTimeUnixNano'])
                assert not span.get('events') and not span.get('links') and not span.get('status', {}).get('message')
                attributes = {a['key']: a['value'] for a in span.get('attributes', [])}
                assert set(attributes) <= {'http.method', 'http.status_code', 'http.route', 'http.request.method', 'http.response.status_code'}
                assert attributes['http.method']['stringValue'] == 'POST' and attributes['http.status_code']['intValue'] == '200'
                groups[span['traceId']].append((attrs['service.name'], span))
    assert len(groups) == 5
    for rows in groups.values():
        assert len(rows) == 3 and len({s['spanId'] for _, s in rows}) == 3
        root = next(s for service, s in rows if service == 'iot-feature-api' and s['kind'] == 2)
        client = next(s for service, s in rows if service == 'iot-feature-api' and s['kind'] == 3)
        model = next(s for service, s in rows if service == 'iot-predictor-api' and s['kind'] == 2)
        assert not root.get('parentSpanId') and client['parentSpanId'] == root['spanId'] and model['parentSpanId'] == client['spanId']
        assert root['name'] == 'POST /v1/machines/{machine_id}:predict' and model['name'] == 'POST /v1/models/{model_name}:predict'
    assert '77eac3a48d7878f1d678cb3a54e1e38b' in groups
    assert all(word not in raw for word in [b'CW_CANARY28B', b'CW_HEADER28B', b'Authorization', b'http.url', b'url.full', b'temperature_mean_10m'])


def main():
    """Verify evidence integrity before recomputing gate, source, rollout, metrics and trace facts."""
    count = manifest('')
    assert manifest('gate') == 61 and manifest('native') == 128
    manifest('runtime-restore')
    seal = source('gate/source/current.json')
    assert seal == read('native/source/api.json') and seal['files'] == read('gate/source-after.json')['files']
    source('native/source/config.json')
    probe = read('native/workload/verification.json')
    assert sha256(historical_blob(ROOT, probe['workload_commit'] + ':tests/load/api_observability.py')).hexdigest() == probe['workload_source_sha256']
    meta = read('gate/current/observed.py.meta')['exit_code_by_key']
    assert len(meta) == 250 and Counter(meta.values()) == {1: 221, 0: 29}
    assert all(k.startswith('services.observed.') and re.search(r'__mutmut_\d+$', k) for k in meta)
    inventory = (BASE / 'gate/current/mutmut-all-results.txt').read_text(encoding='utf-8')
    entries = re.findall(r'^\s*(services\.observed\.\S+): (killed|survived)\s*$', inventory, re.M)
    assert len(entries) == len(meta) and len(dict(entries)) == len(meta)
    assert dict(entries) == {k: 'killed' if v == 1 else 'survived' for k, v in meta.items()}
    gate = read('gate/current/verification.json')
    assert gate['mutation_score_percent'] == 100 * sum(v == 1 for v in meta.values()) / len(meta) > 80
    coverage = read('gate/current/coverage.json')['totals']
    assert coverage['covered_lines'] == coverage['num_statements'] == 74 and coverage['covered_branches'] == coverage['num_branches'] == 16 and coverage['excluded_lines'] == 0
    junit = ET.parse(BASE / 'gate/current/junit.xml')
    cases = junit.findall('.//testcase')
    # pytest emits 296 testcase elements while the suite's tests attribute also counts 14 subtests.
    assert len(cases) == 296 and junit.find('.//testsuite').get('tests') == '310'
    assert all(not any(c.find(n) is not None for n in ['failure', 'error', 'skipped']) for c in cases)
    assert re.search(r'296 passed,.*14 subtests passed', (BASE / 'gate/current/api-tests.txt').read_text())
    spec = importlib.util.spec_from_file_location('serving_audit', ROOT / 'scripts/verify-native-serving.py')
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    pin = json.loads((ROOT / 'docs/evidence/ci-serving-linux/selected-model6/model-pin.json').read_bytes())
    model = json.loads((ROOT / 'docs/evidence/ci-serving-linux/selected-model6/portable.json').read_bytes())
    audit.all_machine_rows(read('native/api-rollout/http-all-machines.json'), model, pin)
    for kind, image in read('native/image-build/verification.json')['images'].items():
        assert image['packaged_sha256']['services/observed.py'] == seal['files']['services/observed.py']
        pods = read('native/api-rollout/' + kind + '-pods.json')['items']
        ready = [p for p in pods if not p['metadata'].get('deletionTimestamp') and any(c['type'] == 'Ready' and c['status'] == 'True' for c in p['status']['conditions'])]
        assert len(ready) == 1
        container = next(c for c in ready[0]['status']['containerStatuses'] if c['name'] in ['api', 'kserve-container'])
        assert container['imageID'] == image['config_digest']
        for path, wanted in image['packaged_sha256'].items():
            if path in seal['files']:
                assert wanted == seal['files'][path]
    assert read('native/baseline-refresh/physical-ttl-before.json')['keys'] == 0
    ttl = read('native/baseline-refresh/physical-ttl-after.json')
    assert ttl['keys'] == len(ttl['pttl_ms']) == 200 and min(ttl['pttl_ms']) > 3300000
    prepared = datetime.fromisoformat(read('native/baseline-state/context.json')['prepared_wall_utc'])
    measured = datetime.fromisoformat(probe['client']['checked_utc'])
    assert 0 < (measured - prepared).total_seconds() < 3600
    permissions = read('native/monitoring-prepare/rbac-permissions.json')
    assert len(permissions) == 5 and [p['allowed'] for p in permissions] == [True, True, False, False, False]
    targets = [t for t in read('native/data-inspection/targets.json')['data']['activeTargets'] if t['labels'].get('job') == 'iot-api']
    assert len(targets) == 3 and all(t['health'] == 'up' and not t['lastError'] for t in targets)
    assert read('native/collector/jaeger-deploy/final-readback-v3/health.json')['healthy'] is True
    metrics_and_ledger()
    traces()
    assert read('native/schedule-restored.json')['after_paused'] is False
    restored = read('runtime-restore/readback.json')
    assert restored['dag']['is_paused'] is False
    assert all(restored['health'][n]['status'] == 'healthy' for n in ['metadatabase', 'scheduler', 'dag_processor'])
    for path in (BASE / 'screenshots').glob('*.jpg'):
        assert path.read_bytes().startswith(b'\xff\xd8\xff')
    print(f'PASS: {count} sealed payloads; 296 main/14 subtests, 74 statements/16 branches, 221/250 mutation; 747 client/932 SDK requests; six panels/28 values per engine; five traces/15 spans; model6 and restored Airflow.')


if __name__ == '__main__':
    main()
