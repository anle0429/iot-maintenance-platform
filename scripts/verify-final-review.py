"""Verify the original after-restart recovery, fresh inference, credential CI and renewed TLS receipts without contacting services."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import ssl
import subprocess
from evidence_integrity import ROOT, source_bindings


def capsule():
    """Require every declared original payload and reject path traversal, extra files and altered bytes."""
    base = ROOT / 'docs/evidence/final-review-live'
    manifest = json.loads((base / 'manifest.json').read_bytes())
    actual = {p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file() and p.name != 'manifest.json'}
    assert actual == set(manifest['files']) and len(actual) == 24
    for name, metadata in manifest['files'].items():
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        raw = (base / name).read_bytes()
        assert len(raw) == metadata['bytes'] and sha256(raw).hexdigest() == metadata['sha256'], name
        assert b'PRIVATE KEY-----' not in raw, name
    return base, lambda name: json.loads((base / name).read_bytes())


def verify():
    """Bind executed operators to immutable Git blobs, recompute inference and preserve scope and historical failures."""
    base, read = capsule(); operators = read('operator-source.json')
    for name, source in operators.items():
        original = subprocess.check_output(['git', 'show', source['commit'] + ':' + name], cwd=ROOT)
        assert sha256(original).hexdigest() == source['sha256'], name
    for name, receipt in [('resume-demo-services.py', 'resume-release/verification.json'), ('refresh-demo-feature.py', 'feature-refresh/verification.json'),
                          ('demo-feature-api.py', 'pc-http/verification.json'), ('renew-demo-certificates.py', 'certificates/verification.json')]:
        assert read(receipt)['passed'] is True and read(receipt)['source_sha256'] == operators['scripts/' + name]['sha256']
    recovery = read('resume-release/verification.json'); steps = recovery['steps']; session = recovery['session']
    start = datetime.fromisoformat(session['last_start_utc']); stop = datetime.fromisoformat(session['auto_stop_utc'])
    assert stop - start == timedelta(hours=8) and session['max_run_seconds'] == 28800
    assert all(code == 200 for code in recovery['health_http'].values()) and len(recovery['health_http']) == 5
    assert steps['monitors']['all_active'] and steps['private_gateway_relay']['socket_active'] and steps['dynamic_logs']['old_positions_preserved']
    assert steps['jenkins']['existing_home_preserved'] and steps['vault']['reinitialized'] is False and steps['vault']['http'] == 200
    credentials = steps['consumer_credentials']; assert credentials['jenkins_updated'] and credentials['private_values_archived'] is False
    for name in ['ci', 'gateway']:
        role = credentials['roles'][name]
        assert role['policy'] == 'iot-' + name and role['token_ttl_seconds'] == 300 and role['secret_id_uses'] == 20
        assert 0 < role['secret_id_ttl_seconds'] <= (stop - datetime.fromisoformat(recovery['checked_utc'])).total_seconds()
    for name in ['resume', 'resume-v2', 'resume-with-roles']:
        assert read(name + '/verification.json')['passed'] is False
    fresh = read('feature-refresh/verification.json'); bindings = source_bindings(fresh['runtime_source'])
    assert fresh['machines'] == 200 and fresh['model_version'] == '6' and fresh['ready']['ready']
    prepared = datetime.fromisoformat(fresh['prepared_wall_utc']); expiry = datetime.fromisoformat(fresh['lease_expires_utc'])
    assert expiry - prepared == timedelta(hours=1)
    assert read('feature-refresh/physical-ttl-before.json')['keys'] == 0
    after = read('feature-refresh/physical-ttl-after.json'); assert after['keys'] == 200 and min(after['pttl_ms']) > 600_000
    container = read('feature-refresh/materialization-container.json')
    assert container['state']['ExitCode'] == 0 and not container['state']['OOMKilled'] and container['user'] == '10001:10001'
    assert container['memory_bytes'] == container['memory_swap_bytes'] == 8 * 1024**3 and container['cpu_nanos'] == 1_000_000_000
    http = read('pc-http/http.json'); proof = read('pc-http/verification.json')
    expected = [('GET','/health/live',200),('GET','/health/ready',200),('GET','/v1/machines/P-001/features',200),
                ('POST','/v1/machines/P-001:predict',200),('POST','/v1/machines/P-001:predict',200),('GET','/v1/machines/P-006/features',200),
                ('POST','/v1/machines/P-006:predict',409),('GET','/v1/machines/P-9999/features',404),('GET','/v1/machines/invalid/features',422)]
    assert [(row['method'], row['path'], row['status']) for row in http] == expected
    assert http[3]['body'] == http[4]['body'] and http[2]['body']['prepared_wall_utc'] == fresh['prepared_wall_utc']
    spec = importlib.util.spec_from_file_location('feature_demo', ROOT / 'scripts/demo-feature-api.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    model_base = ROOT / 'docs/evidence/final-demo-linux'
    model = json.loads((model_base / 'portable.json').read_bytes()); pin = json.loads((model_base / 'model-pin.json').read_bytes())
    assert sha256((model_base / 'portable.json').read_bytes()).hexdigest() == pin['portable_sha256']
    score = module.check_prediction(http[2]['body'], http[3]['body'], model, pin, datetime.fromisoformat(proof['checked_utc']))
    assert score == proof['risk_score'] and proof['actual_http_requests'] == 9
    ci = read('vault-ci-v3/verification.json'); consumer = read('vault-ci-v3/consumer.json'); build = read('vault-ci-v3/build.json')
    assert ci['passed'] and ci['build'] == build['number'] == 3 and ci['result'] == build['result'] == 'SUCCESS' and not build['building']
    ci_source = read('vault-ci-v3/source.json'); assert ci['source_commit'] == ci_source['commit']; ci_bindings = source_bindings(ci_source)
    assert consumer['passed'] and consumer['role_policy'] == 'iot-ci' and consumer['airflow_login_http'] == 201 and consumer['airflow_dag_list_http'] == 200
    assert consumer['cross_gateway_read_http'] == consumer['revoked_read_http'] == 403
    gateway = read('vault-ci-v3/gateway-role.json'); assert gateway['cross_read_http'] == 403 and gateway['version'] == 2
    certificate = read('certificates/verification.json'); hosts = {'metrics.iot.test','logs.iot.test','traces.iot.test','features.iot.test'}
    assert set(gateway['gateway_https']) == set(certificate['gateway_http']) == hosts
    assert all(code == 200 for code in gateway['gateway_https'].values()) and all(code == 200 for code in certificate['gateway_http'].values())
    assert certificate['gateway_all_routes_200'] and certificate['private_key_bytes_unchanged'] and certificate['old_bytes_backed_up']
    assert certificate['vault_health_http'] == 200 and certificate['vault_reinitialized'] is False
    assert certificate['vault_cluster_id'] == steps['vault']['cluster_id'] == '63e1d9cc-7eb1-c7a7-a48d-b11a3ac107f9'
    pem = (base / 'certificates/gateway.crt').read_text(); der = ssl.PEM_cert_to_DER_cert(pem)
    fingerprint = sha256(der).hexdigest()
    assert fingerprint == certificate['expected_gateway_peer_der_sha256']
    assert set(certificate['actual_gateway_peer_der_sha256']) == hosts and set(certificate['actual_gateway_peer_der_sha256'].values()) == {fingerprint}
    for name, path in [('ca','outputs/gateway-28/deploy/ca.crt'),('gateway','outputs/gateway-28/deploy/server.crt'),('vault','data/runtime/vault-server-28/server.crt')]:
        assert sha256((base / f'certificates/{name}.crt').read_bytes()).hexdigest() == certificate['after'][path]['sha256']
        fields = dict(line.split('=',1) for line in certificate['after'][path]['metadata'][:2])
        valid_from = datetime.strptime(fields['notBefore'], '%b %d %H:%M:%S %Y GMT').replace(tzinfo=timezone.utc)
        valid_until = datetime.strptime(fields['notAfter'], '%b %d %H:%M:%S %Y GMT').replace(tzinfo=timezone.utc)
        assert valid_from <= datetime.fromisoformat(certificate['checked_utc']) < valid_until
        assert valid_until > datetime(2026,11,1,tzinfo=timezone.utc)
    return {'passed': True, 'native_payloads':21, 'public_payloads':24, 'operator_git_bindings':4, 'runtime_git_bindings':bindings,
            'ci_git_bindings':ci_bindings, 'actual_http_cases':9, 'vault_ci_build':3, 'verified_tls_hosts':4, 'risk_score':score,
            'scope':'Frozen historical after-restart proof; not current readiness, a grade, public DNS, full100GB ML or laptop validation.'}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
