"""Verify original private MCP HTTPS requests, failed predecessors and post-restart UI against native TLS audit bytes."""
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import json
import ssl
import tarfile
from integration_evidence import ROOT, capsule, read
from git_sources import git_blobs

MANIFEST = 'd6e9e51a0c8a54a0cab360b32e0b27fdb186b84fb24d6dc438a31426096977c9'


def original_archive(base, filename, prefix, size, digest, inventory_digest, count):
    """Authenticate the unchanged compressed artifact, safe member inventory and every original payload."""
    raw = (base/filename).read_bytes()
    assert len(raw) == size and sha256(raw).hexdigest() == digest
    bodies = {}
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for item in archive:
            if not item.isfile():
                assert item.isdir(); continue
            name = PurePosixPath(item.name).relative_to(prefix)
            assert not name.is_absolute() and '..' not in name.parts and item.size < 10000000 and name.as_posix() not in bodies
            bodies[name.as_posix()] = archive.extractfile(item).read()
    assert sha256(bodies['manifest.json']).hexdigest() == inventory_digest
    inventory = json.loads(bodies['manifest.json'])
    assert len(inventory['files']) == count and set(bodies) == set(inventory['files']) | {'manifest.json'}
    for name, pin in inventory['files'].items():
        assert len(bodies[name]) == pin['bytes'] and sha256(bodies[name]).hexdigest() == pin['sha256']
        assert b'-----BEGIN PRIVATE KEY-----' not in bodies[name]
    assert inventory['current_native_credentials_scanned'] and inventory['vault_root_and_unseal_scanned']
    return bodies


def decoded_ui(raw, key):
    """Read the actual response shown in an original complete accessibility snapshot, without fabricating wire formatting."""
    text = raw.decode(); position = text.index('"'+key+'"'); start = text.rfind('{', 0, position)
    return json.JSONDecoder().raw_decode(text[start:])[0]


def verify():
    """Check source, real authorization/TLS outcomes, preservation, native SDK results and two untouched UI captures."""
    base, count = capsule('mcp-gateway', MANIFEST); assert count == 18
    bodies = original_archive(base, 'native-receipts.tar.gz', 'mcp-gateway-capture-30a', 211290,
        '3928ced92e2a6c4cb3bbd90def53714112676228069697a1445f079f76a774c9',
        '66541891208543682f922eb3dccfbc36a0905f2a2afd7bd88d7a5efe0ab426e7', 178)
    supplement = original_archive(base, 'native-viewer-receipts.tar.gz', 'mcp-gateway-view-capture-30c', 19148,
        '49df98ec7c988ab70152ba07ee66ee43df705f1abacc36560f0ec9ef35bf7595',
        '6f11fb5e1d2827330ce5c0151d8c0f5cc2e3c8b21ff390ee1269963ba349e65c', 20)
    for filename, expected in [('native-archive.json', 178), ('native-viewer-archive.json', 20)]:
        meta = read(base, filename)
        assert meta['native_payloads'] == expected and not meta['recompressed_or_rewritten_on_pc']
        assert meta['actual_native_credentials_scanned'] and meta['vault_root_and_unseal_scanned'] and meta['pc_actual_gpu_join_credential_scanned']

    def native(name):
        """Read JSON only after authenticating all archived bytes."""
        return json.loads(bodies[name])

    source_count = 0
    for folder, expected in [('3a889305', 4), ('537fbde4', 6), ('d749040d', 7), ('7c08ce4c', 7)]:
        prefix = 'source/mcp-gateway-'+folder+'/'; seal = native(prefix+'manifest.json'); assert len(seal['files']) == expected
        committed = git_blobs(ROOT, seal['commit'], seal['files'])
        for name, pin in seal['files'].items():
            original = bodies[prefix+'tree/'+name]
            assert sha256(original).hexdigest() == pin and committed[name] == original
            if folder == '7c08ce4c': assert (ROOT/name).read_bytes().replace(b'\r\n', b'\n') == original
        source_count += expected

    before = 'execution/mcp-gateway-deploy-30b/before-'
    for kind in ['deployments', 'services', 'networkpolicies', 'ingresses', 'roles', 'rolebindings', 'sandboxagents', 'workerpools', 'modelconfigs']:
        old = native(before+kind+'.json')['items']; current = {v['metadata']['uid']: v for v in native('current/'+kind+'.json')['items']}
        for item in old:
            if kind == 'ingresses' and item['metadata']['namespace'] != 'iot-agents': continue
            now = current[item['metadata']['uid']]
            assert all(now.get(key) == item.get(key) for key in ['spec', 'rules', 'subjects', 'roleRef'])
            assert now['metadata'].get('annotations') == item['metadata'].get('annotations')
    for name in ['agent-gateway-auth', 'agent-gateway-tls', 'iot-retrieval-mcp-auth', 'iot-drift-mcp-auth']:
        assert native(before+'secret-'+name+'.json') == native('current/'+name+'-identity.json')
    correction = native('execution/mcp-gateway-correction-30a/verification.json'); assert correction['passed']
    original = native('execution/mcp-gateway-correction-30a/before-ingress.json')
    accepted = native('execution/mcp-gateway-correction-30a/after-ingress.json')
    assert original['metadata']['uid'] == accepted['metadata']['uid'] == correction['ingress_uid']
    assert original['spec'] == accepted['spec'] and accepted['spec']['rules'][0]['host'] == 'retrieval.iot.test'
    annotations = accepted['metadata']['annotations']
    assert annotations['nginx.org/upstream-vhost'] == 'iot-retrieval-mcp.iot-agents.svc.cluster.local'
    assert 'nginx.org/basic-auth-secret' not in annotations and 'nginx.org/proxy-set-headers' not in annotations
    for name in ['gateway', 'policy']:
        old = native('execution/mcp-gateway-correction-30a/before-'+name+'.json')
        new = native('execution/mcp-gateway-correction-30a/after-'+name+'.json')
        assert old['metadata']['uid'] == new['metadata']['uid'] and old['spec'] == new['spec']
    leaf = sha256(ssl.PEM_cert_to_DER_cert(bodies['execution/mcp-gateway-deploy-30b/server.crt'].decode())).hexdigest()
    assert leaf == correction['tls']['leaf_sha256']
    rows = native('execution/mcp-gateway-probe-30a/run/http.json'); assert len(rows) == 13
    by_case = {r['case']: r for r in rows}; assert len(by_case) == len(rows)
    for name in ['missing-token', 'incorrect-token', 'basic-is-not-bearer', 'nonstandard-header-is-not-auth', 'other-role-token']:
        assert by_case[name]['status'] == 401
    for name, code in [('plain-http-redirect', 308), ('native-docs', 200), ('native-openapi', 200), ('machine-features', 200),
                       ('machine-prediction', 200), ('knowledge-rest', 200), ('schema-rejection', 422), ('foreign-origin-rejected', 403)]:
        assert by_case[name]['status'] == code
    for row in rows:
        assert sha256(row['body'].encode()).hexdigest() == row['body_sha256']
        if row['tls']: assert row['tls']['ca_verified'] and row['tls']['hostname_verified'] and row['tls']['leaf_sha256'] == leaf
    responses = {name: json.loads(by_case[case]['body']) for name, case in [('get_machine_features', 'machine-features'), ('predict_failure', 'machine-prediction'), ('search_knowledge', 'knowledge-rest')]}
    sdk = native('execution/mcp-gateway-probe-30a/run/mcp-sdk.json'); assert len(sdk['calls']) == 3
    assert {r['tool'] for r in sdk['calls']} == set(responses)
    for row in sdk['calls']: assert not row['response']['isError'] and json.loads(row['response']['content'][0]['text']) == responses[row['tool']]
    assert responses['predict_failure']['feature_trace'] == responses['get_machine_features'] and responses['predict_failure']['model']['version'] == '6'
    negative = native('execution/mcp-gateway-probe-30a/run/tls-negative.json'); assert len(negative) == 2 and all(v['rejected'] for v in negative)
    assert {v['case'] for v in negative} == {'untrusted-ca', 'wrong-hostname'}
    container = native('execution/mcp-gateway-probe-30a/container.json')
    assert not container['state']['Running'] and container['state']['ExitCode'] == 0 and not container['state']['OOMKilled']
    assert container['memory'] == 512*1024**2 and container['network'] == 'host' and container['user'] == '10001:10001'
    refresh = native('execution/feature-refresh-mcp-30a/verification.json'); probe = native('execution/mcp-gateway-probe-30a/run/verification.json')
    prepared = datetime.fromisoformat(refresh['prepared_wall_utc']); expiry = datetime.fromisoformat(refresh['lease_expires_utc'])
    assert refresh['passed'] and refresh['machines'] == 200 and prepared < datetime.fromisoformat(probe['checked_utc']) < expiry
    assert native('execution/feature-refresh-mcp-30a/physical-ttl-before.json')['keys'] == 0
    assert native('execution/feature-refresh-mcp-30a/physical-ttl-after.json')['keys'] == 200
    assert [v['status'] for v in native('execution/mcp-gateway-view-30b/http-boundary.json')] == [200, 403, 403, 403]
    assert b'KeyError' in bodies['execution/mcp-gateway-30a/deploy-console.txt']
    assert any(e.get('reason') == 'Rejected' and 'invalid character in header value' in e.get('message', '') for e in native('execution/mcp-gateway-correction-30a/before-events.json')['items'])

    audit = [json.loads(line) for line in supplement['viewer/access.jsonl'].splitlines()]
    index = read(base, 'ui/index.json'); assert len(index['captures']) == 2 and not index['screenshots_edited'] and not index['browser_ca_bypass']
    assert b'InvocationID=accbc66c955f4f489cdaebf4d3aba224' in supplement['viewer-unit.txt']
    assert b'ActiveState=active' in supplement['viewer-unit.txt']
    for name, key, path in [('features', 'machine_id', '/v1/machines/P-001/features'), ('knowledge', 'matches', '/v1/knowledge:search')]:
        wire = (base/('ui/'+name+'.raw.json')).read_bytes(); decoded = read(base, 'ui/'+name+'-decoded.json')
        assert decoded['response'] == json.loads(wire) == decoded_ui((base/('ui/'+name+'.ax.txt')).read_bytes(), key)
        record = decoded['native_transport_record']; assert record in audit and record['path'] == path and record['status'] == 200
        assert record['response_bytes'] == len(wire) and record['served_response_sha256'] == sha256(wire).hexdigest()
        assert record['host'] == 'retrieval.iot.test' and record['ca_verified'] and record['hostname_verified']
        assert record['leaf_sha256'] == leaf and not record['body_edited'] and not record['credentials_logged']
        assert prepared < datetime.fromisoformat(record['utc']) < expiry
        picture = (base/('ui/'+name+'.jpg')).read_bytes(); assert picture.startswith(b'\xff\xd8') and picture.endswith(b'\xff\xd9')
    observed_ingress = json.loads(supplement['current/ingress-iot-mcp-retrieval.json'])
    assert observed_ingress['metadata']['uid'] == accepted['metadata']['uid'] and observed_ingress['spec'] == accepted['spec']
    assert observed_ingress['metadata']['annotations'] == annotations
    service = json.loads(supplement['current/service-iot-gateway.json'])
    assert service['spec']['type'] == 'ClusterIP' and service['spec']['clusterIP'] == correction['gateway_ip'] and not service['spec'].get('externalIPs')
    live = json.loads(supplement['current/retrieval-pods.json'])['items']; assert len(live) == 2
    assert all(any(v['type'] == 'Ready' and v['status'] == 'True' for v in p['status']['conditions']) for p in live)
    return {'passed': True, 'native_payloads': 198, 'historical_source_bindings': source_count, 'http_requests': 13,
            'credential_rejections': 5, 'tls_rejections': 2, 'native_mcp_tools': 3, 'original_screenshots': 2,
            'ui_bodies_match_native_tls_audit': True,
            'scope': 'Recorded private MCP HTTPS and post-restart UI. Historical requests and genuine lease; not continuous readiness, browser CA trust, public DNS, load performance or full coursework completion.'}


if __name__ == '__main__': print(json.dumps(verify(), indent=2))
