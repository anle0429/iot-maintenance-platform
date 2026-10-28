"""Independently audit retained native serving CI, complete mutation provenance, A/B math, rollback and load reports."""
from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree
import csv, io, json, math, re, struct, subprocess, tarfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'docs/evidence/ci-serving-linux'


def read(name):
    """Decode an already hash-checked saved receipt, never contact a running service."""
    return json.loads((BASE / name).read_bytes())


def digest(raw):
    """Hash original bytes, including native LF and Unicode mutation identities."""
    return sha256(raw).hexdigest()


def canonical(value):
    """Reproduce the public semantic JSON encoding used for HTTP and durable ledger comparisons."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def source_packet(source):
    """Reconstruct a deterministic sealed packet from historical Git objects rather than today's worktree."""
    packet = io.BytesIO()
    with tarfile.open(fileobj=packet, mode='w') as archive:
        for name, expected in sorted(source['files'].items()):
            assert not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts
            raw = subprocess.check_output(['git', 'show', source['commit'] + ':' + name], cwd=ROOT)
            assert digest(raw) == expected, name
            item = tarfile.TarInfo('tree/' + name); item.size = len(raw); item.mode = 0o644
            archive.addfile(item, io.BytesIO(raw))
    assert digest(packet.getvalue()) == source['archive_sha256']


def jpeg_dimensions(raw):
    """Read original JPEG dimensions without decoding, resizing or altering any pixel."""
    assert raw[:2] == b'\xff\xd8'; offset = 2
    while offset < len(raw):
        assert raw[offset] == 255; marker = raw[offset + 1]; offset += 2
        if marker == 0xda: break
        size = struct.unpack('>H', raw[offset:offset + 2])[0]
        if marker in [0xc0, 0xc1, 0xc2]:
            height, width = struct.unpack('>HH', raw[offset + 3:offset + 7]); return width, height
        offset += size
    raise AssertionError('Missing original JPEG dimensions')


def prediction(row, model, pin):
    """Calculate portable logistic arithmetic independently and reject stopped/future/foreign feature traces."""
    trace = row['feature_trace']; values = trace['features']; actual = row['prediction']
    assert trace['machine_id'] == row['machine_id'] and trace['clock_mode'] == 'simulation'
    assert trace['operating_context']['status'] == 'operating'
    assert trace['protocol_sha256'] == pin['protocol_sha256']
    assert type(row['model']['production']) is bool and all(row['model'][k] == v for k, v in pin.items())
    clock = datetime.fromisoformat(trace['as_of']); event = datetime.fromisoformat(trace['feature_event_ts'])
    assert 0 <= (clock - event).total_seconds() <= 600
    assert datetime.fromisoformat(trace['operating_context']['received_ts']) <= clock
    assert trace['feature_age_seconds'] == (clock - event).total_seconds()
    assert set(values) == set(model['features']) and all(type(v) in [int, float] and math.isfinite(v) for v in values.values())
    z = model['intercept'] + sum(((values[n] - m) / s if s else 0) * w
        for n, m, s, w in zip(model['features'], model['mean'], model['std'], model['coefficients'], strict=True))
    score = 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))
    assert math.isclose(score, actual['risk_score'], rel_tol=0, abs_tol=1e-12)
    assert type(actual['predicted_failure']) is int and actual['predicted_failure'] == int(score >= pin['threshold'])


def all_machine_rows(rows, model, pin):
    """Check all200 actual feature reads and195 eligible predictions, retaining five stopped conflicts."""
    assert len(rows) == len({r['machine_id'] for r in rows}) == 200
    operating = stopped = 0
    for row in rows:
        field = 'status' if 'status' in row else 'prediction_status'
        assert set(row) == {'machine_id', 'features', 'response', field}
        status = row[field]
        if status == 200:
            response = row['response']; prediction({**response, 'machine_id': row['machine_id']}, model, pin)
            assert response['feature_trace'] == row['features']; operating += 1
        else:
            assert status == 409 and row['features']['operating_context']['status'] != 'operating'; stopped += 1
    assert (operating, stopped) == (195, 5)


def compare_series(samples, wanted, experiment, panel):
    """Require each real query engine to return the complete finite ledger-derived value table."""
    actual = {}
    for labels, value in samples:
        assert labels.get('experiment') == (None if panel == 4 else experiment)
        key = (labels['variant'], labels['version'], labels.get('feature', ''))
        assert key not in actual; actual[key] = float(value)
    assert actual.keys() == wanted.keys()
    assert all(math.isfinite(actual[k]) and math.isclose(actual[k], v, rel_tol=1e-10, abs_tol=1e-12) for k, v in wanted.items())


def main():
    """Audit a named historical native execution; do not confuse offline readbacks with a live deployment."""
    manifest = read('manifest.json'); proof = read('verification.json'); commit = proof['source_commit']
    assert manifest['format'] == 'iot-native-serving-proof-v1' and manifest['source_commit'] == commit
    assert commit == '99d6dcc2994f55f06133eace53ecff523f6fc3cb' and len(manifest['files']) == 172
    for name, item in manifest['files'].items():
        assert not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts
        raw = (BASE / name).read_bytes(); assert len(raw) == item['bytes'] and digest(raw) == item['sha256'], name
    assert sum(x['bytes'] for x in manifest['files'].values()) == manifest['payload_bytes'] == 11639527
    native = read('native-capture.json'); assert len(native['files']) == 161
    omitted = set(proof['omitted_native_bookkeeping']); assert omitted == {'ci/tests/coverage-html/.gitignore', 'ci/tests/coverage-html/status.json'}
    for name, item in native['files'].items():
        if name not in omitted: assert manifest['files'][name] == {k: item[k] for k in ['bytes', 'sha256']}, name
    source, parent = read('source-current.json'), read('source-mutation-parent.json')
    assert source['commit'] == commit and len(source['files']) == 75 and len(parent['files']) == 73
    source_packet(source); source_packet(parent); source_packet(read('failed-ab6/source-manifest.json'))
    controls = {'ml/ab_native.py', 'tests/test_ab_ci.py', 'scripts/ci-serving.py', 'scripts/test-serving-ci.py', 'scripts/package-serving-release.py', 'infra/ci/Serving.Jenkinsfile'}
    protected = set(parent['files']) - controls
    assert len(protected) == 67 and all(source['files'][n] == parent['files'][n] for n in protected)
    assert set(source['files']) - set(parent['files']) == {'ml/serving_gate_reuse.py', 'tests/test_serving_gate_reuse.py'}
    gate, old = read('ci/tests/verification.json'), read('mutation-parent5/tests/verification.json')
    assert gate['passed'] and old['passed'] and not old.get('mutation_provenance')
    provenance = gate['mutation_provenance']; assert provenance == read('ci/tests/mutation-provenance.json')
    assert provenance['parent_release'] == 'serve-jenkins-5' and provenance['parent_commit'] == parent['commit'] and provenance['current_commit'] == commit
    assert provenance['original_verification_sha256'] == digest((BASE / 'mutation-parent5/tests/verification.json').read_bytes())
    assert provenance['protected_files'] == {n: source['files'][n] for n in sorted(protected)}
    assert (BASE / 'ci/tests/requirements-installed.lock').read_bytes() == (BASE / 'mutation-parent5/tests/requirements-installed.lock').read_bytes()
    mutations = {}; metadata = read('mutation-parent5/tests/mutation-metadata-manifest.json')
    for name, item in metadata['files'].items():
        path = 'mutation-parent5/tests/mutation-metadata/' + name + '.meta'; raw = (BASE / path).read_bytes()
        assert len(raw) == item['bytes'] and digest(raw) == item['sha256']
        codes = json.loads(raw)['exit_code_by_key']; assert set(codes.values()) <= {0, 1} and not set(mutations) & set(codes)
        mutations.update(codes)
    modules = {n.split('.x', 1)[0].replace('.', '/') + '.py' for n in mutations}
    assert modules == {'services/ab_api/app.py', 'services/ab_api/runtime.py', 'services/ab_api/metrics.py', 'services/feature_api/app.py',
        'services/feature_api/runtime.py', 'services/predictor/runtime.py', 'services/predictor/binding.py'}
    assert len(mutations) == 1582 and Counter(mutations.values()) == {1: 1309, 0: 273}
    for prefix, receipt in [('ci/tests/', gate), ('mutation-parent5/tests/', old)]:
        for name, h in receipt['result_sha256'].items(): assert digest((BASE / (prefix + name)).read_bytes()) == h
        inventory = dict(re.findall(r'^\s*(\S+): (killed|survived)\s*$', (BASE / (prefix + 'mutmut-all-results.txt')).read_text(encoding='utf-8'), re.M))
        assert inventory == {n: 'killed' if code == 1 else 'survived' for n, code in mutations.items()}
        stats = read(prefix + 'mutmut-cicd-stats.json'); assert stats['total'] == 1582 and stats['killed'] == 1309 and stats['survived'] == 273
        assert all(v == 0 for k, v in stats.items() if k not in ['total', 'killed', 'survived'])
        assert math.isclose(receipt['mutation_score_percent'], 1309 / 1582 * 100) and receipt['mutation_score_percent'] > 80
        coverage = read(prefix + 'coverage.json'); totals = coverage['totals']
        assert totals['covered_lines'] == totals['num_statements'] == 425 and totals['excluded_lines'] == 0
        assert totals['covered_branches'] == 125 and totals['num_branches'] == 128 and 125 / 128 > .9
        assert sum(f['summary']['num_statements'] for f in coverage['files'].values()) == 425
        suites = list(ElementTree.fromstring((BASE / (prefix + 'junit.xml')).read_bytes()).iter('testsuite'))
        assert sum(int(s.attrib['tests']) for s in suites) == 208 and not any(int(s.attrib.get(k, 0)) for s in suites for k in ['errors', 'failures', 'skipped'])
    build = read('ci-terminal/build.json'); assert (build['number'], build['result'], build['building'], build['duration'], build['queueId']) == (8, 'SUCCESS', False, 307949, 26)
    assert {p['name']: p['value'] for a in build['actions'] for p in a.get('parameters', [])} == {'SOURCE_COMMIT': commit, 'MODEL_VERSION': '6', 'API_GATE_BUILD': '5'}
    assert (BASE / 'ci-terminal/jenkins-console.txt').read_text(encoding='utf-8').rstrip().endswith('Finished: SUCCESS')
    assert '104 passed' in (BASE / 'ci/tests/contracts.txt').read_text(encoding='utf-8') and '208 passed' in (BASE / 'ci/tests/api-tests.txt').read_text(encoding='utf-8')
    for name in ['tests', 'select', 'stage', 'ab', 'switch', 'verify', 'restore-scaling']:
        state = read('ci/' + name + '-container.json'); assert state['commit'] == commit and state['state']['ExitCode'] == 0 and not state['state']['OOMKilled'] and not state['state']['Running']
    assert read('ci/tests-container.json')['memory_bytes'] == 4 * 1024 ** 3 and read('ci/tests-container.json')['nano_cpus'] == 2_000_000_000
    definition = read('job-definition-readback.json'); assert definition['sandbox'] == 'true' and definition['definition_sha256'] == source['files']['infra/ci/Serving.Jenkinsfile']
    assert definition['parameter_defaults'] == {'SOURCE_COMMIT': commit, 'MODEL_VERSION': '6', 'API_GATE_BUILD': ''}
    session, admitted = read('vm-session.json'), read('ci/admission.json')
    assert session['max_run_seconds'] == 28800 and admitted['auto_stop_utc'] == session['auto_stop_utc']
    assert admitted['seconds_before_actual_stop'] >= 5400 and admitted['ram_available_bytes'] >= 12 * 1024 ** 3 and min(admitted['disk_free_bytes']) >= 20_000_000_000
    selected = read('selected-model6/model-pin.json'); candidate = read('selected-model6/portable.json')
    legacy = json.loads(subprocess.check_output(['git', 'show', commit + ':models/pump-failure-v1/portable.json'], cwd=ROOT))
    assert selected['version'] == '6' and selected['production'] is True and digest((BASE / 'selected-model6/portable.json').read_bytes()) == selected['portable_sha256']
    assert read('ci/selection.json')['pin'] == read('mutation-parent5/selection.json')['pin'] == selected
    images = read('ci/build.json')['images']; selection = read('ci/selection.json'); pair = read('ci/plan.json'); stage = read('ci/staged.json')
    assert read('ci/build.json')['commit'] == pair['commit'] == stage['commit'] == commit and read('ci/build.json')['pin'] == selected
    for image in images.values():
        assert re.fullmatch('sha256:[a-f0-9]{64}', image['config_digest']) and image['tag'].endswith(':serve-jenkins-8')
        for name, h in image['packaged_sha256'].items():
            if name.startswith('models/release/'):
                asset = name.removeprefix('models/release/'); assert h == selection['bundle_sha256'][asset] == digest((BASE / 'selected-model6' / asset).read_bytes())
            else: assert h == source['files'][name], name
    for artifact, kind, container in [('ci/feature-pods.json', 'feature', 'api'), ('ci/model-pods.json', 'predictor', 'kserve-container'), ('ab-server-readbacks.json', 'ab', 'api')]:
        pods = read(artifact)['objects']['pods'] if kind == 'ab' else read(artifact)
        live = [p for p in pods['items'] if not p['metadata'].get('deletionTimestamp')]; assert live
        for pod in live:
            spec = next(c for c in pod['spec']['containers'] if c['name'] == container)
            status = next(c for c in pod['status']['containerStatuses'] if c['name'] == container)
            identity = status['imageID'].split('://')[-1].split('@')[-1]
            assert spec['image'] == images[kind]['tag'] and identity == images[kind]['config_digest'] and status['ready'] and status['restartCount'] == 0
    ab = read('ci/ab-verification.json'); rows = ab['observations']; variants = ['baseline', 'candidate']
    assert ab['passed'] and ab['capture_kind'] == 'native-kubernetes-http' and ab['commit'] == commit and ab['release'] == 'serve-jenkins-8' and ab['ground_truth_used'] is False
    assert ab['candidate_pin'] == selected and ab['baseline_pin']['version'] == '1'
    assert ab['staged_isvc_uid'] == stage['isvc_uid'] == read('ci/inferenceservice.json')['metadata']['uid']
    assert stage['plan_sha256'] == digest((BASE / 'ci/plan.json').read_bytes()) and stage['values_sha256'] == digest((BASE / 'ci/feature-values.json').read_bytes())
    assert len(rows) == len({r['request_id'] for r in rows}) == 780
    assert read('ci/ab-initial-identities.json') == read('ci/ab-final-identities.json')
    assert ab['ab_plan_sha256'] == digest((BASE / 'ci/ab-plan.json').read_bytes())
    client = {r['request_id']: r for r in read('ci/ab-http-probe.json')['client_observations']}; cycles = {}; expected = {i: {} for i in range(1, 7)}
    reference_raw = next(o['data']['reference.json'] for o in read('ci/ab-resources.json')['items'] if o['kind'] == 'ConfigMap' and 'reference.json' in o.get('data', {}))
    reference = json.loads(reference_raw); assert digest(reference_raw.encode()) == ab['reference_raw_sha256'] and digest(canonical(reference)) == ab['reference_id']
    for row in rows:
        variant = 'baseline' if sha256((ab['experiment'] + '\0' + row['machine_id']).encode()).digest()[0] < 128 else 'candidate'
        assert row['variant'] == variant and row['status'] == 200 and math.isfinite(row['latency_ms']) and row['latency_ms'] >= 0
        experiment, cycle, machine = row['request_id'].split(':'); assert experiment == ab['experiment'] and machine == row['machine_id']
        cycles.setdefault(machine, set()).add(int(cycle)); prediction(row, legacy if variant == 'baseline' else candidate, ab[variant + '_pin'])
        assert client[row['request_id']]['semantic_response_sha256'] == digest(canonical(row)) and client[row['request_id']]['client_elapsed_ms'] >= 0
    assert len(cycles) == 195 and all(c == set(range(4)) for c in cycles.values())
    for variant in variants:
        group = [r for r in rows if r['variant'] == variant]; version = ab[variant + '_pin']['version']; key = (variant, version, '')
        unique = {(r['machine_id'], r['feature_trace']['feature_event_ts']): r['feature_trace']['features'] for r in group}
        assert len(group) == proof['cohort_counts'][variant] and len(unique) == proof['unique_snapshots'][variant]
        expected[1][key] = len(group); expected[2][key] = 0; expected[3][key] = len(unique); expected[5][key] = sum(r['prediction']['risk_score'] for r in group) / len(group)
        lower = count_before = 0; rank = .95 * len(group)
        for upper in [.005, .01, .025, .05, .1, .25, .5, 1, 5]:
            count = sum(r['latency_ms'] / 1000 <= upper for r in group)
            if count >= rank: expected[4][key] = lower + (upper - lower) * (rank - count_before) / (count - count_before); break
            lower, count_before = upper, count
        else: expected[4][key] = 5.
        drift = ab['drift'][variant]; assert drift['unique_rows'] == len(unique) and drift['reference_id'] == ab['reference_id']
        for field, value in drift['features'].items():
            values = sorted(r[field] for r in reference['samples']); cuts = sorted(set(values[min(len(values) - 1, len(values) * i // 10)] for i in range(1, 10)))
            rc, cc = [0] * (len(cuts) + 1), [0] * (len(cuts) + 1)
            for v in values: rc[sum(c <= v for c in cuts)] += 1
            for r in unique.values(): cc[sum(c <= r[field] for c in cuts)] += 1
            p = [(n + .5) / (sum(rc) + .5 * len(rc)) for n in rc]; q = [(n + .5) / (sum(cc) + .5 * len(cc)) for n in cc]
            psi = sum((b - a) * math.log(b / a) for a, b in zip(p, q, strict=True))
            assert cuts == value['cuts'] and rc == value['reference_counts'] and cc == value['current_counts'] and math.isclose(psi, value['psi'], abs_tol=1e-12)
            expected[6][(variant, version, field)] = psi
        before, after = [(BASE / f'ci/ab-{variant}-predictor-{when}.txt').read_text(encoding='utf-8') for when in ['before', 'after']]
        counter = lambda t: float(re.search(r'^iot_predictor_requests_total\{outcome="success"\} ([0-9.e+]+)$', t, re.M)[1])
        assert counter(after) - counter(before) == ab['predictor_counter_deltas'][variant] >= len(group)
    assert read('ci/ab-http-probe.json')['replay_checks'] == 40 and sum(len(x) for x in expected.values()) == 26
    ledger = read('independent-hdd-ledger.json'); assert ledger['rows'] == 780 and ledger['semantic_rows_sha256'] == digest(canonical(rows)) == ab['retained_ledger']['semantic_rows_sha256']
    assert ledger['seal']['context']['machines'].keys() == cycles.keys() | {m for m, v in ledger['seal']['context']['machines'].items() if v['status'] != 'operating'}
    objects = read('ab-server-readbacks.json')['objects']; native = ab['ab_native']; plan = read('ci/ab-plan.json')
    assert objects['deployment']['metadata']['uid'] == native['deployment_uid'] and objects['service']['metadata']['uid'] == native['service_uid']
    for obj in plan['objects']:
        actual = objects[obj['kind'] + '-' + obj['metadata']['name']]
        if obj['kind'] == 'ConfigMap':
            wanted = native['inputs'][obj['metadata']['name']]; assert actual['metadata']['uid'] == wanted['uid'] and actual['immutable']
            assert {k: digest(v.encode()) for k, v in actual['data'].items()} == wanted['sha256']
        elif obj['kind'] == 'PersistentVolume': assert actual['metadata']['uid'] == native['pv_uid'] and actual['status']['phase'] == 'Bound' and actual['spec']['persistentVolumeReclaimPolicy'] == 'Retain'
        else: assert actual['metadata']['uid'] == native['pvc_uid'] and actual['status']['phase'] == 'Bound'
    pv = next(o for key, o in objects.items() if key.startswith('PersistentVolume-'))
    pvc = next(o for key, o in objects.items() if key.startswith('PersistentVolumeClaim-'))
    assert pvc['spec']['volumeName'] == pv['metadata']['name'] and pv['spec']['claimRef']['uid'] == pvc['metadata']['uid']
    assert pvc['spec']['storageClassName'] == '' and pv['spec']['local']['path'].endswith('/serve-jenkins-8/runtime/ab-ledger')
    assert ledger['database_relative_path'] == 'data/ci/releases/serve-jenkins-8/runtime/ab-ledger/trials.sqlite'
    for phase in ['before-ab', 'ab-recheck', 'before-switch']:
        health = read('ci/baseline-health-' + phase + '.json'); assert health['passed'] and health['cache_keys'] == len(health['features']) == 200
        assert min(health['cache_ttl_ms']) >= 900000 and health['minimum_context_lease_seconds'] >= 900
        assert health['feature_deployment_uid'] == ab['active_feature_before']['uid'] and health['feature_generation'] == ab['active_feature_before']['generation']
    original = read('ci/ab-scaling-original.json'); restored = read('ci/ab-scaling-restored.json'); assert restored['passed']
    for name, item in original['models'].items():
        pinned = read('ci/ab-scaling-pinned.json')['states'][name]; final = restored['states'][name]
        assert pinned['isvc']['metadata']['uid'] == final['isvc']['metadata']['uid'] == item['uid']
        assert pinned['isvc']['spec']['predictor']['minReplicas'] == pinned['isvc']['spec']['predictor']['maxReplicas'] == 1
        assert {k: final['isvc']['spec']['predictor'][k] for k in item['policy']} == item['policy'] == {'minReplicas': 1, 'maxReplicas': 2}
    all_machine_rows(read('ci/http-all-machines.json'), candidate, selected)
    for name, model, pin in [('candidate-before', candidate, selected), ('baseline-restored', legacy, ab['baseline_pin']), ('candidate-restored', candidate, selected)]:
        assert read('rollback/' + name + '.json')['passed']; all_machine_rows(read('rollback/' + name + '-http.json'), model, pin)
    assert read('rollback/verification.json')['path'] == ['6', '1', '6']
    dashboard = read('monitoring/grafana-dashboard-readback.json')['dashboard']; assert len(dashboard['panels']) == 6
    assert read('monitoring/verification.json')['native_ab_report_sha256'] == digest((BASE / 'ci/ab-verification.json').read_bytes())
    for panel, result in zip(dashboard['panels'], read('monitoring/panel-queries.json'), strict=True):
        number = panel['id']; assert result['panel'] == number and result['expr'] == panel['targets'][0]['expr'].replace('$experiment', ab['experiment'])
        compare_series([(r['metric'], r['value'][1]) for r in result['prometheus']['data']['result']], expected[number], ab['experiment'], number)
        plotted = []
        for frame in result['grafana']['results']['A']['frames']:
            for i, field in enumerate(frame['schema']['fields']):
                if field['type'] == 'number':
                    values = frame['data']['values'][i]; assert len(values) == 1; plotted.append((field['labels'], values[0]))
        compare_series(plotted, expected[number], ab['experiment'], number)
    for users, count, csv_count, p95 in [(1, 598, 591, 7), (10, 5780, 5706, 28)]:
        summary = read(f'load/users-{users}-summary.json'); assert summary['passed'] and summary['requests'] == count and summary['failures'] == 0 and summary['p95_ms'] == p95
        assert summary['sla'] == {'minimum_requests': 100, 'maximum_failures': 0, 'p95_ms': 500, 'clock_mode': 'simulation'}
        text = (BASE / f'load/users-{users}.html').read_text(encoding='utf-8'); match = re.search(r'window\.templateArgs\s*=\s*', text)
        html = json.JSONDecoder().raw_decode(text[match.end():])[0]; aggregate = next(r for r in html['requests_statistics'] if r['name'] == 'Aggregated')
        assert aggregate['num_requests'] == count and aggregate['num_failures'] == 0 and aggregate['response_time_percentile_0.95'] == p95
        assert math.isclose(aggregate['total_rps'], summary['requests_per_second']) and not html['failures_statistics'] and not html['exceptions_statistics']
        stats = list(csv.DictReader(io.StringIO((BASE / f'load/users-{users}_stats.csv').read_text(encoding='utf-8')))); aggregate = next(r for r in stats if r['Name'] == 'Aggregated')
        # Locust2.47 writes CSV once per second and closes it without a final rewrite.
        # Preserve this earlier snapshot; final HTML and quitting-SLA summary agree above.
        assert int(aggregate['Request Count']) == csv_count < count and int(aggregate['Failure Count']) == 0 and float(aggregate['95%']) == p95
        history = list(csv.DictReader(io.StringIO((BASE / f'load/users-{users}_stats_history.csv').read_text(encoding='utf-8'))))
        assert int(history[-1]['Total Request Count']) == csv_count and int(history[-1]['Total Failure Count']) == 0
        assert int(history[-1]['Timestamp']) <= datetime.fromisoformat(html['end_time'].replace('Z', '+00:00')).timestamp()
    failed = read('failed-fallback7/baseline-redis-diagnostic.json'); observation = failed['observation']
    assert observation['cache_keys'] == 0 and observation['stats']['expired_keys'] == 600 and observation['stats']['evicted_keys'] == 0
    assert 0 < (datetime.fromisoformat(failed['checked_utc']) - datetime.fromisoformat(observation['context']['prepared_wall_utc'])).total_seconds() < 3600
    assert 'context deadline exceeded' in (BASE / 'failed-fallback7/rollback-to-baseline.txt').read_text(encoding='utf-8')
    assert read('failed-ab6/build.json')['result'] == 'FAILURE' and 'A/B or active workload changed during the experiment' in (BASE / 'failed-ab6/ab.txt').read_text(encoding='utf-8')
    for name, item in read('screen-captures.json').items():
        raw = (BASE / 'screenshots' / name).read_bytes(); assert digest(raw) == item['sha256'] and jpeg_dimensions(raw) == (item['width'], item['height'])
    print('PASS172payload /75+73 sealed Git inputs /complete1582mutants /208API+coverage /780native trials+40replays /HDD+native UIDs /600controlled rollback reads /26values each engine /two Locust HTML+CSV /10original screenshots. Native source99d6dcc only; offline audit, not live deployment or model quality.')


if __name__ == '__main__':
    main()
