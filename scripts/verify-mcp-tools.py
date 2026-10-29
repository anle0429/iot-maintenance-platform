"""Verify source-bound native MCP transport, rollout, API quality and authenticated load evidence offline."""
from collections import Counter
from hashlib import sha256
import csv
import json
import math
import xml.etree.ElementTree as ET
from integration_evidence import ROOT, capsule, read, sources, exited

MANIFEST_SHA256 = '21c4cb7e7a3b391f14af3e4e893c0b0555143d498c5b69be8ddb2e19752b78db'


def payload(call):
    """Read the actual business JSON inside an original successful MCP SDK result."""
    assert call['response']['isError'] is False
    return json.loads(call['response']['content'][0]['text'])


def verify():
    """Reconcile raw SDK responses, native process states, exact source identities and independent report totals."""
    base, count = capsule('mcp-tools', MANIFEST_SHA256)
    curation = read(base, 'curation.json')
    excluded = curation['excluded_nonessential']
    assert len(excluded) == 56
    assert sum(name.startswith('capacity/source/.kube/cache/discovery/') for name in excluded) == 53
    assert sum(name.startswith('capacity/source/.kube/cache/http/') for name in excluded) == 2
    assert 'initial-mutation/native/proof/.coverage' in excluded
    assert curation['previous_manifest_sha256'] == 'f6fdd3de2a7bff42aa06a24584eb1c2f37a3cd9142695ee407bf7b99973cf906'
    assert curation['native_archive_sha256'] == '6d8aa7d3ade8a727df360b274042cf55db0a77b56b4444bec16935e379197b56'
    historical_differences = {}
    for prefix in ['success', 'load', 'quality-baseline']:
        recorded = sources(base, prefix)
        changed = [name for name, pin in recorded['files'].items()
                   if sha256((ROOT / name).read_bytes()).hexdigest() != pin]
        # The timestamp validation fix has separate local quality evidence; retain these original deployed bytes.
        assert set(changed) <= {'services/mcp_tools/contracts.py'}
        historical_differences[prefix] = changed
    seal = sources(base, 'success')
    for prefix in ['initial-namespace-failure', 'initial-feature-failure', 'initial-client-failure', 'initial-capacity-failure', 'initial-mutation']:
        sources(base, prefix)
    result = read(base, 'success/native/verification.json')
    digest = sha256(json.dumps(seal, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert result['passed'] and result['source_sha256'] == digest
    assert result['actual_native_mcp_probes'] == 4 and result['controlled_atomic_rollbacks'] == 2
    assert result['replicas_per_role'] == 2
    build = read(base, 'success/native/build.json')
    assert build['image_id'] == result['image_id'] and build['source_sha256'] == digest
    exited(read(base, 'success/native/tests-container.stdout')[0])
    chunks = {row['chunk_id']: row for row in read(base, 'success/state/chunks.json')}
    assert len(chunks) == 18
    observed_calls = 0
    for role, expected in [('retrieval', {'get_machine_features', 'predict_failure', 'search_knowledge'}), ('drift', {'assess_feature_drift'})]:
        responses = []
        for phase in ['probe', 'probe-after-rollback']:
            report = read(base, f'success/native/{role}-{phase}.stdout')
            assert (base / f'success/native/{role}-{phase}.exit').read_text(encoding='utf-8').strip() == '0'
            assert report['passed'] and report['wrong_token_status'] == 401
            assert report['initialized']['protocolVersion'] == '2025-11-25'
            assert {row['name'] for row in report['tools']['tools']} == expected
            calls = report['calls']; observed_calls += len(calls)
            if role == 'retrieval':
                assert len(calls) == 9 and payload(calls[0]) == payload(calls[1])
                features = payload(calls[0]); prediction = payload(calls[2])
                assert features['machine_id'] == 'P-001' and len(features['features']) == 8
                assert features['clock_mode'] == 'simulation' and prediction['feature_trace'] == features
                assert prediction['model']['version'] == '6' and prediction['model']['production'] is True
                assert prediction['prediction']['predicted_failure'] == int(prediction['prediction']['risk_score'] >= prediction['model']['threshold'])
                assert prediction['model']['protocol_sha256'] == features['protocol_sha256']
                assert [calls[i]['response']['isError'] for i in [3, 4, 5, 8]] == [True] * 4
                found = payload(calls[6]); assert found == payload(calls[7]) and len(found['matches']) == 3
                assert 'monitoring' in [row['document_id'] for row in found['matches']]
                for row in found['matches']:
                    expected_row = chunks[row['chunk_id']]
                    for key in ['text', 'document_id', 'revision', 'source_sha256', 'source_file', 'section']:
                        assert row[key] == expected_row[key]
                    source = ROOT / 'knowledge' / row['source_file']
                    assert sha256(source.read_bytes()).hexdigest() == row['source_sha256'] and row['text'] in source.read_text(encoding='utf-8')
                    assert math.isfinite(row['cosine_similarity']) and -1 <= row['cosine_similarity'] <= 1.001
            else:
                assert len(calls) == 3 and calls[2]['response']['isError'] is True
                first = payload(calls[0]); assert first == payload(calls[1]) and first['unique_rows'] == 32
                assert first['reference_id'] == result['reference_id'] and first['lineage']['model_version'] == '3'
                fraction = sum(row['psi'] >= .2 for row in first['features'].values()) / 8
                assert fraction == first['drifted_feature_fraction'] == .75
                assert all(row['drifted'] == (row['psi'] >= .2) for row in first['features'].values())
                assert first['drift_detected'] == (fraction >= .25)
                assert 'no ground truth, accuracy or concept-drift claim' in first['scope']
                assert first['assessment_sha256'] == report['assessment_sha256']
                responses.append(first)
        if role == 'drift':
            assert responses[0] == responses[1]
        for phase in ['install', 'rolling']:
            assert (base / f'success/native/{role}-{phase}.exit').read_text(encoding='utf-8').strip() == '0'
        assert (base / f'success/native/{role}-bad-upgrade.exit').read_text(encoding='utf-8').strip() != '0'
        actual = read(base, f'success/native/{role}-values-after.stdout')
        assert actual['image']['tag'] == 'mcp-' + digest[:12] and actual['revisionMarker'] == 'mcp-foundation-29e-rolling'
        history = read(base, f'readback/{role}-history.stdout')
        assert history[-1]['status'] == 'deployed' and history[-1]['description'].startswith('Rollback to ')
        assert history[-2]['status'] == 'failed'
    assert observed_calls == 24
    objects = read(base, 'success/native/resources-after.stdout')['items']
    deployments = [row for row in objects if row['kind'] == 'Deployment']
    assert len(deployments) == 2
    for row in deployments:
        assert row['spec']['replicas'] == row['status']['availableReplicas'] == 2
        assert row['spec']['strategy']['rollingUpdate'] == {'maxSurge': 1, 'maxUnavailable': 0}
        pod = row['spec']['template']['spec']
        assert not pod['automountServiceAccountToken'] and pod['securityContext']['runAsNonRoot']
        assert pod['containers'][0]['securityContext']['readOnlyRootFilesystem']
    assert all(row['spec']['type'] == 'ClusterIP' for row in objects if row['kind'] == 'Service')
    assert len([row for row in objects if row['kind'] == 'NetworkPolicy']) == 2
    capacity = read(base, 'capacity/native/verification.json')
    assert capacity['passed'] and capacity['pod_capacity_before'] == 60 and capacity['pod_capacity_after'] == 110
    assert capacity['same_cpu_memory'] and capacity['base_config_unchanged'] and capacity['second_apply_changed'] == 0
    assert 'changed=0' in (base / 'capacity/native/ansible-idempotent.stdout').read_text(encoding='utf-8')
    capacity_source = base / 'capacity/source/agent-capacity.yml'
    assert sha256(capacity_source.read_bytes()).hexdigest() == read(base, 'capacity/native/source.json')['sha256']
    assert capacity_source.read_bytes() == (ROOT / 'infra/ansible/agent-capacity.yml').read_bytes()
    junit = ET.parse(base / 'quality-baseline/native/junit.xml').getroot()
    cases = list(junit.iter('testcase')); assert len(cases) == 79
    assert not list(junit.iter('failure')) and not list(junit.iter('error')) and not list(junit.iter('skipped'))
    assert any(row.attrib['name'] == 'test_cohort_replay_is_independent_of_input_order' for row in cases)
    assert '12 passing, 0 failing, and 0 invalid test cases' in (base / 'quality-baseline/native/tests.stdout').read_text(encoding='utf-8')
    coverage = read(base, 'quality-baseline/native/coverage.json')['totals']
    assert coverage['num_statements'] == 354 and coverage['covered_lines'] == 346
    assert 100 * coverage['covered_lines'] / coverage['num_statements'] > 90
    assert coverage['num_branches'] == 106 and coverage['covered_branches'] == 96
    assert 100 * coverage['covered_branches'] / coverage['num_branches'] > 90 and coverage['percent_covered'] > 90
    mutation = read(base, 'initial-mutation/native/proof/result/verification.json')
    categories = Counter()
    for path in (base / 'initial-mutation/native/proof/result').glob('*.meta'):
        categories.update(json.loads(path.read_bytes())['exit_code_by_key'].values())
    assert categories == {1: 531, 0: 398, -24: 16}
    assert mutation['stats']['total'] == sum(categories.values()) == 945 and mutation['complete_inventory']
    assert not mutation['passed'] and mutation['mutation_score_percent'] == 100 * categories[1] / 945 < 80
    assert not json.loads((base / 'initial-mutation/native/container.json').read_bytes())[0]['State']['OOMKilled']
    assert not read(base, 'initial-mutation/native/container.json')[0]['State']['Running']
    expanded = read(base, 'quality-final/native/verification.json')
    updated_categories = Counter()
    for path in (base / 'quality-final/native').glob('*.meta'):
        updated_categories.update(json.loads(path.read_bytes())['exit_code_by_key'].values())
    assert updated_categories == {1: 594, 0: 343, -24: 8}
    assert expanded['complete_inventory'] and expanded['stats']['total'] == sum(updated_categories.values()) == 945
    assert expanded['mutation_score_percent'] == 100 * 594 / 945 < 80 and not expanded['passed']
    exited(read(base, 'quality-final/native/container.json')[0], 1)
    assert 'ActiveState=failed' in (base / 'quality-final/native/unit.txt').read_text(encoding='utf-8')
    binding = read(base, 'quality-final/native/baseline-binding.json')
    assert binding['passed']
    assert binding['source_manifest_sha256'] == sha256((base / 'quality-baseline/source/manifest.json').read_bytes()).hexdigest()
    for name, pin in binding['earlier_baseline_unchanged'].items():
        assert sha256((base / 'quality-baseline/native' / name).read_bytes()).hexdigest() == pin
    load = read(base, 'load/native/verification.json')
    requests = 0
    for users in [1, 8]:
        row = read(base, f'load/native/users-{users}-summary.json')
        assert row['passed'] and row['sla'] == {'minimum_requests': 100, 'maximum_failures': 0, 'p95_ms': 1000}
        assert row['requests'] >= 100 and row['failures'] == 0 and row['p95_ms'] <= 1000
        table = list(csv.DictReader((base / f'load/native/users-{users}_stats.csv').read_text(encoding='utf-8').splitlines()))
        total = next(item for item in table if item['Name'] == 'Aggregated')
        # Locust writes this periodic CSV before its final quitting-event summary and HTML export.
        assert 0 < int(total['Request Count']) <= row['requests'] and int(total['Failure Count']) == 0
        assert float(total['95%']) == row['p95_ms']
        native_html = (base / f'load/native/users-{users}.html').read_text(encoding='utf-8')
        data = json.loads(next(line.split('=', 1)[1].strip().removesuffix(';') for line in native_html.splitlines() if 'window.templateArgs =' in line))
        html_total = next(item for item in data['requests_statistics'] if item['name'] == 'Aggregated')
        assert html_total['num_requests'] == row['requests'] and html_total['num_failures'] == 0
        assert html_total['response_time_percentile_0.95'] == row['p95_ms'] and not data['exceptions_statistics']
        requests += row['requests']
    assert requests == load['measured_requests'] == load['per_replica_counter_delta'] == 2658
    counters = {}
    for phase in ['before', 'after']:
        values = {}
        for path in (base / 'load/native').glob('metrics-' + phase + '-*.prom'):
            rows = [line for line in path.read_text(encoding='utf-8').splitlines() if line.startswith('iot_mcp_tool_calls_total{')
                    and 'tool="get_machine_features"' in line and 'result="success"' in line]
            values[path.name.split(phase + '-', 1)[1]] = sum(float(line.rsplit(' ', 1)[1]) for line in rows)
        assert len(values) == 2; counters[phase] = values
    assert set(counters['before']) == set(counters['after'])
    assert sum(counters['after'].values()) - sum(counters['before'].values()) == requests
    assert read(base, 'privacy.json')['passed']
    for path in (base / 'ui').glob('*.jpg'):
        assert path.read_bytes().startswith(b'\xff\xd8\xff')
    assert len(list((base / 'ui').glob('*.jpg'))) == 9
    return {'passed': True, 'payloads': count, 'native_mcp_calls': observed_calls, 'replicas_per_role': 2,
            'controlled_atomic_rollbacks': 2, 'native_tests': 79, 'line_coverage_percent': 100 * 346 / 354,
            'branch_coverage_percent': 100 * 96 / 106, 'locust_requests': requests,
            'initial_mutation_gate_passed': False, 'initial_mutation_score_percent': mutation['mutation_score_percent'],
            'expanded_mutation_gate_passed': False, 'expanded_mutation_score_percent': expanded['mutation_score_percent'],
            'recorded_api_source_matches_current': not any(historical_differences.values()),
            'changed_since_recorded_deployment': historical_differences,
            'scope': 'Historical private MCP/REST transport, deployment, baseline API quality and feature-read load. Both recorded VM mutation gates failed. A later timestamp validation fix and local Cosmic Ray run have separate evidence; this capture does not prove their deployment. Agents, sandbox, CI and full LLM telemetry remain incomplete.'}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
