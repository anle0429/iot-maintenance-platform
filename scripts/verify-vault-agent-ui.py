"""Authenticate native Vault metadata UI, private TLS and denial receipts without reading any live secret."""
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import json
import tarfile
from git_sources import git_blobs
from integration_evidence import ROOT, capsule, read


def verify():
    """Reconcile original captures, source history, unchanged Vault identity and metadata-only readbacks."""
    base, count = capsule('vault-agent-ui', 'ac5c967e05b8eeaf0597afc1b20ca97eb358dbe327ad6d1b24fedb7923b613aa')
    assert count == 19
    raw = (base/'native-receipts.tar.gz').read_bytes()
    assert len(raw) == 52636 and sha256(raw).hexdigest() == 'd692e1c4456488337be6a0c84fb16a1eb3ac5d4672a537dd598df56dffac2eb1'
    files = {}
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            if member.isdir(): continue
            assert member.isfile() and member.size < 10000000
            path = PurePosixPath(member.name).relative_to('vault-ui-capture-30a')
            assert not path.is_absolute() and '..' not in path.parts and path.as_posix() not in files
            files[path.as_posix()] = archive.extractfile(member).read()
    inventory = json.loads(files['manifest.json'])
    assert sha256(files['manifest.json']).hexdigest() == '865644773786b1ed746c6f96c03cdf0d394ad8a54e9055fdbfc7219aac1172c9'
    assert len(inventory['files']) == 93 and set(files) == set(inventory['files']) | {'manifest.json'}
    for name, pin in inventory['files'].items():
        assert sha256(files[name]).hexdigest() == pin['sha256'] and len(files[name]) == pin['bytes']
        assert b'-----BEGIN PRIVATE KEY-----' not in files[name]
    meta = read(base, 'native-archive.json')
    assert all(meta[k] for k in ['current_native_credentials_scanned', 'vault_root_and_unseal_scanned', 'pc_actual_gpu_join_credential_scanned'])
    assert not meta['recompressed_or_rewritten_on_pc'] and not meta['secret_values_published']

    def native(name):
        """Decode only authenticated original native JSON."""
        return json.loads(files[name])

    seal = native('source/manifest.json'); assert seal['commit'] == 'a1bc0a46472e57b69fc24b3ee19307945ff0d4b5' and len(seal['files']) == 2
    for name, original in git_blobs(ROOT, seal['commit'], seal['files']).items():
        assert sha256(original).hexdigest() == seal['files'][name] and original == files['source/tree/'+name]
        assert original == (ROOT/name).read_bytes().replace(b'\r\n', b'\n')
    for letter in 'abcdefgh':
        prefix = 'execution/vault-ui-30'+letter+'/'
        if prefix+'source.json' not in files: continue
        old = native(prefix+'source.json'); committed = git_blobs(ROOT, old['commit'], old['files'])
        for name, pin in old['files'].items(): assert sha256(committed[name]).hexdigest() == pin
        assert files[prefix+'viewer.py'] == committed['scripts/view-vault-metadata.py']
    assert b'Ran 5 tests' in files['native-tests.stderr'] and files['native-tests.stderr'].rstrip().endswith(b'OK')
    proof = native('verification.json'); assert proof == read(base, 'verification.json') and proof['passed'] and proof['native_unit_tests'] == 5
    assert not proof['secret_values_read'] and not proof['vault_policies_or_roles_written']
    assert files['vault-service-before.txt'] == files['vault-service-after.txt'] and b'ActiveState=active' in files['vault-service-before.txt']
    health = native('current/health.json'); assert health['cluster_id'] == '63e1d9cc-7eb1-c7a7-a48d-b11a3ac107f9' and health['initialized'] and not health['sealed']
    for role in ['gateway', 'services']:
        assert (base/(role+'-metadata.json')).read_bytes() == files['readback/'+role+'-metadata.json']
        value = native('readback/'+role+'-metadata.json')['data']
        assert value['current_version'] == 1 and set(value['versions']) == {'1'} and not value['versions']['1']['destroyed']
        assert not value['versions']['1']['deletion_time'] and value['created_time'].startswith('2026-10-29T19:19:0')
        policy = native('current/'+role+'-policy.json'); assert policy == read(base, role+'-policy.json')
        assert policy['data']['policy'].strip() == (ROOT/'infra/security'/('vault-agent-'+role+'.hcl')).read_text().strip()
        config = native('current/'+role+'-role.json')['data']
        assert config['token_policies'] == ['iot-agent-'+role] and config['token_ttl'] == 300 and config['token_max_ttl'] == 900
    rows = native('readbacks.json'); assert rows == read(base, 'readbacks.json') and len(rows) == 7
    for row in rows:
        body = files['readback/'+row['case']+'.json']; assert sha256(body).hexdigest() == row['body_sha256']
        assert row['status'] == (403 if row['case'].endswith('-denied') else 200)
    assert native('readback/agent-list.json')['data']['keys'] == ['gateway', 'services']
    transport = []
    for public, original in [('transport-ui.jsonl', 'execution/vault-ui-30g/transport.jsonl'), ('transport-metadata.jsonl', 'execution/vault-ui-30h/transport.jsonl')]:
        assert (base/public).read_bytes() == files[original]
        transport += [json.loads(line) for line in files[original].splitlines()]
    tls = [row for row in transport if row.get('ca_verified')]
    assert tls and all(row['hostname_verified'] and not row['body_edited'] and not row['credentials_logged'] for row in tls)
    assert {row['leaf_sha256'] for row in tls} == {'e8a419978419e981189eae83731cb9de8a6fd01eaa9429fe52057a358c42a54b'}
    assert any(row['path'].startswith('/ui/') and row['status'] == 200 and row['response_bytes'] > 200000 for row in tls)
    assert any(row['path'] == '/v1/iot/metadata/agents%2Fservices' and row['status'] == 200 for row in tls)
    for row in rows:
        if row['status'] != 403: continue
        path = row['path'].split('?')[0]
        assert any(v['path'] == path and v['method'] == row['method'] and v['status'] == 403 and v.get('upstream_contacted') is False for v in transport)
    # Earlier empty identity responses and refused UI metadata aliases remain original unsuccessful observations.
    old = [json.loads(line) for line in files['execution/vault-ui-30b/transport.jsonl'].splitlines()]
    assert any(v['path'] == '/ui/' and v['response_bytes'] == 0 for v in old)
    unit = files['current-view-unit.txt']
    for part in ['InvocationID=5a4339214af246cfa11e184ca80a5ff7', 'ActiveState=active', 'User=nobody', 'Group=nogroup', 'RuntimeMaxUSec=1h', 'ProtectSystem=strict', 'NoNewPrivileges=yes']:
        assert part.encode() in unit
    ui = read(base, 'ui/index.json'); assert len(ui['captures']) == 4 and ui['root_browser_session_closed']
    assert not ui['screenshots_edited'] and not ui['secret_values_displayed']
    for row in ui['captures']:
        assert row['unedited'] and row['format'] == 'JPEG'
        raw = (base/'ui'/(row['name']+'.jpg')).read_bytes(); assert raw.startswith(b'\xff\xd8') and raw.endswith(b'\xff\xd9')
        text = (base/'ui'/(row['name']+'.ax.txt')).read_text(encoding='utf-8')
        if row['name'] == 'dashboard': assert 'root token' in text and 'Vault v2.2.1' in text
        elif row['name'] == 'agent-paths': assert 'gateway' in text and 'services' in text and '1–2 of 2' in text
        elif row['name'] == 'services-version': assert 'Version  1' in text and 'Current' in text and 'Oct 29, 2026 02:19 PM' in text
        else: assert 'Secret metadata' in text and 'No custom metadata' in text
    return {'passed': True, 'native_payloads': 93, 'original_ui_captures': 4, 'native_guard_tests': 5, 'safe_readbacks': 3,
            'guarded_denials': 4, 'scope': 'Existing root operator metadata UI; unchanged retained Vault; earlier AppRole consumer authorization remains a separate proof.'}


if __name__ == '__main__': print(json.dumps(verify(), indent=2))
