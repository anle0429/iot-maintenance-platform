"""Verify the timestamp fix's native Linux tests, deployed image, real MCP calls and controlled Helm rollback."""
from hashlib import sha256
import json
import re
from integration_evidence import ROOT, capsule, read, sources, exited

MANIFEST_SHA256 = '92bae197a9884ad9759064202c19da1bf9321a73bb2462972a09cb89433a946f'


def payload(call):
    """Decode actual successful SDK tool output, preserving errors as separate original calls."""
    assert call['response']['isError'] is False
    return json.loads(call['response']['content'][0]['text'])


def verify():
    """Bind the local-tested modules to native build/test/deployment receipts without substituting them for agent proof."""
    base, count = capsule('mcp-quality-native', MANIFEST_SHA256)
    assert count == 171
    seal = sources(base, '.', current=True)
    assert len(seal['files']) == 64
    local = ROOT / 'docs/evidence/mcp-quality/source/manifest.json'
    assert seal['tested_candidate_manifest_sha256'] == sha256(local.read_bytes()).hexdigest()
    assert all(seal['files'][name] == pin for name, pin in json.loads(local.read_bytes())['files'].items())
    unit = (base / 'unit.txt').read_text()
    assert 'ActiveState=inactive\n' in unit and 'ExecMainStatus=0\n' in unit and 'Result=success\n' in unit
    # A finished transient unit can be garbage-collected; authenticate its original invocation from the journal.
    process, process_count = capsule('mcp-quality-process', 'cebaf704be81375a2a39db8df5379a821570e126e6c81b7c862d01bcb090870e')
    assert process_count == 3
    journal = [json.loads(line) for line in (process / 'journal.jsonl').read_text().splitlines()]
    capture = read(process, 'capture.json')
    invocation = '36be62af556f49e4b0d9a2d3ca9ea32a'
    assert capture['passed'] and capture['credential_scan_passed'] and capture['invocation_id'] == invocation
    assert capture['journal_sha256'] == sha256((process / 'journal.jsonl').read_bytes()).hexdigest()
    original_report = json.loads('\n'.join(row['MESSAGE'] for row in journal if row.get('_SYSTEMD_INVOCATION_ID') == invocation))
    assert original_report == read(base, 'native/verification.json')
    assert any('Deactivated successfully' in row.get('MESSAGE', '') for row in journal)
    digest = sha256(json.dumps(seal, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    build = read(base, 'native/build.json'); result = read(base, 'native/verification.json')
    assert build['passed'] and result['passed'] and build['source_sha256'] == result['source_sha256'] == digest
    assert build['image_id'] == result['image_id'] == 'sha256:939c4201356b974bd7754cd274f42dd9b928049820e6f58086ca0a8c2a1c6db2'
    assert build['image'] == 'iot-coursework-mcp:mcp-' + digest[:12]
    container = read(base, 'native/tests-container.stdout')[0]; exited(container)
    assert container['Config']['Labels']['iot.source.sha256'] == digest
    assert container['Config']['User'] == '10001:10001' and container['HostConfig']['ReadonlyRootfs']
    assert container['HostConfig']['NetworkMode'] == 'none' and container['HostConfig']['Memory'] == 2 * 1024**3
    assert (base / 'native/tests.exit').read_text().strip() == '0'
    assert re.search(r'\b132 passed\b', (base / 'native/tests.stdout').read_text())
    coverage = read(base, 'native/tests/coverage.json')['totals']
    assert coverage == build['coverage']
    line = 100 * coverage['covered_lines'] / coverage['num_statements']
    branch = 100 * coverage['covered_branches'] / coverage['num_branches']
    assert line > 90 and branch > 90 and coverage['percent_covered'] > 90
    earlier = read(ROOT / 'docs/evidence/mcp-tools', 'success/native/snapshot.json')
    assert read(base, 'native/snapshot.json') == earlier
    calls = 0
    for role in ['retrieval', 'drift']:
        for phase in ['probe', 'probe-after-rollback']:
            assert (base / f'native/{role}-{phase}.exit').read_text().strip() == '0'
            probe = read(base, f'native/{role}-{phase}.stdout')
            assert probe['passed'] and probe['wrong_token_status'] == 401
            assert probe['initialized']['protocolVersion'] == '2025-11-25'
            rows = probe['calls']; calls += len(rows)
            if role == 'retrieval':
                assert len(rows) == 9
                features = payload(rows[0]); assert features == payload(rows[1])
                prediction = payload(rows[2])
                assert features['machine_id'] == 'P-001' and prediction['feature_trace'] == features
                assert prediction['model']['version'] == '6' and prediction['model']['production']
                assert prediction['prediction']['predicted_failure'] == int(prediction['prediction']['risk_score'] >= prediction['model']['threshold'])
                assert all(rows[i]['response']['isError'] for i in [3, 4, 5, 8])
                found = payload(rows[6]); assert found == payload(rows[7]) and len(found['matches']) == 3
                for passage in found['matches']:
                    source = ROOT / 'knowledge' / passage['source_file']
                    assert sha256(source.read_bytes()).hexdigest() == passage['source_sha256']
                    assert passage['text'] in source.read_text(encoding='utf-8')
            else:
                assert len(rows) == 3 and rows[2]['response']['isError']
                drift = payload(rows[0]); assert drift == payload(rows[1])
                assert drift['unique_rows'] == 32 and drift['reference_id'] == result['reference_id']
                assert sum(row['psi'] >= .2 for row in drift['features'].values()) == 6
                assert drift['drifted_feature_fraction'] == .75 and drift['drift_detected']
        for phase in ['install', 'rolling']:
            assert (base / f'native/{role}-{phase}.exit').read_text().strip() == '0'
        assert (base / f'native/{role}-bad-upgrade.exit').read_text().strip() != '0'
        values = read(base, f'native/{role}-values-after.stdout')
        assert values['image']['tag'] == 'mcp-' + digest[:12]
        assert values['revisionMarker'] == 'mcp-quality-29a-rolling'
        history = read(base, f'readback/{role}-history.json')
        assert history[-1]['status'] == 'deployed' and history[-1]['description'].startswith('Rollback to ')
        assert history[-2]['status'] == 'failed'
    assert calls == 24 and result['actual_native_mcp_probes'] == 4 and result['controlled_atomic_rollbacks'] == 2
    objects = read(base, 'native/resources-after.stdout')['items']
    deployments = [v for v in objects if v['kind'] == 'Deployment' and v['metadata']['name'] in {'iot-retrieval-mcp', 'iot-drift-mcp'}]
    assert len(deployments) == 2
    for deployment in deployments:
        assert deployment['spec']['replicas'] == deployment['status']['availableReplicas'] == 2
        pod = deployment['spec']['template']['spec']
        assert pod['containers'][0]['image'] == build['image'] and not pod['automountServiceAccountToken']
        assert pod['containers'][0]['securityContext']['readOnlyRootFilesystem']
    curation = read(base, 'curation.json')
    assert curation['credential_scan_passed']
    assert [row['path'] for row in curation['excluded_nonessential']] == ['tests/.coverage']
    return {'passed': True, 'payloads': count + process_count, 'source_files': len(seal['files']), 'native_tests': 132,
        'line_coverage_percent': line, 'branch_coverage_percent': branch, 'native_tool_calls': calls,
        'native_mcp_probes': 4, 'controlled_atomic_rollbacks': 2, 'replicas_per_role': 2,
        'current_source_matches_native_release': True, 'full_agent_acceptance': False, 'coursework_complete': False,
        'scope': 'Native Linux API tests, same source as the local mutation candidate, deployed MCP image, authenticated SDK calls and rollback. Historical load remains a separate versioned measurement; conversations, sandbox boundaries, autoscaling, CI and telemetry remain open.'}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
