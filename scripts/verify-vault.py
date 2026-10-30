"""Audit real Vault AppRole consumers, HMAC audit records, credential rotation and Raft restart without secrets or network."""
from git_sources import historical_blob
from collections import defaultdict
from evidence_integrity import current_source_bindings
from hashlib import sha256
from pathlib import Path
import json,re,ssl,subprocess
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'docs/evidence/vault-linux'


def read(name):
    """Decode only original payloads whose exact bytes are checked by the manifest."""
    return json.loads((BASE/name).read_bytes())


def main():
    """Recalculate policy/access/version/audit invariants without recovering any private value."""
    manifest=read('manifest.json');files={p.relative_to(BASE).as_posix() for p in BASE.rglob('*') if p.is_file() and p!=BASE/'manifest.json'};assert files==set(manifest['files']) and len(files)==81
    for n,v in manifest['files'].items():
        assert not Path(n).is_absolute() and '..' not in Path(n).parts
        raw=(BASE/n).read_bytes();assert len(raw)==v['bytes'] and sha256(raw).hexdigest()==v['sha256'] and b'PRIVATE KEY-----' not in raw,n
    native=read('native-manifest.json');assert len(native['files'])==72 and all(manifest['files'][n]==v for n,v in native['files'].items())
    supplement=read('final-readback/manifest.json');assert len(supplement['files'])==5 and all(manifest['files']['final-readback/'+n]==v for n,v in supplement['files'].items())
    total=0
    for path in (BASE/'sources').glob('*.json'):
        seal=json.loads(path.read_bytes())
        for n,h in seal['files'].items():assert sha256(historical_blob(ROOT, seal['commit']+':'+n)).hexdigest()==h;total+=1
    assert total==41
    for number,commit,result,duration in [(1,'b390efe8c99189d7615bb1275354471a923b6a47','FAILURE',3282),(2,'a4ef8fdcba62babea563fc388aa8b86e067191fc','SUCCESS',2977)]:
        seal=read('sources/ci-'+str(number)+'.json');assert seal['commit']==commit and len(seal['files'])==10
        after=read('ci-'+str(number)+'/source-after.json');assert after['commit']==commit and after['files']==seal['files']
        job=read('ci-'+str(number)+'/job.json');assert job['number']==number and job['building'] is False and job['result']==result and job['duration']==duration
        params={p['name']:p['value'] for a in job['actions'] for p in a.get('parameters',[])};assert params=={'SOURCE_COMMIT':commit}
    current_source_bindings(read('sources/ci-2.json'))
    stages=read('ci-2/stages.json');assert stages['status']=='SUCCESS' and len(stages['stages'])==4 and all(s['status']=='SUCCESS' for s in stages['stages'])
    text=(BASE/'ci-2/console.txt').read_text();assert 'Ran 3 tests' in text and 'Finished: SUCCESS' in text and 'Masking supported pattern matches of $IOT_VAULT_APPROLE' in text
    proof=read('ci-2/verification.json');assert proof['passed'] and proof['consumer']=='Jenkins' and proof['authentication']=='AppRole' and proof['lease']=={'lease_duration':300,'renewable':True,'token_policies':['iot-ci']}
    assert proof['secret_version']==1 and proof['airflow_login_http']==201 and proof['airflow_dag_list_http']==200 and proof['cross_gateway_read_http']==proof['revoked_read_http']==403 and proof['returned_dag_ids']==['iot_ci_dp1_bronze']
    assert '201' in read('airflow-token-contract.json')['responses'] and read('airflow-token-contract.json')['mounted_route']=='/auth/token'
    for name in ['ci','gateway']:
        policy=read('final-readback/policy-'+name+'.json')['data']['policy'];assert policy==(ROOT/('infra/security/vault-'+name+'.hcl')).read_text()
        clauses=re.findall(r'path "([^"]+)"\s*\{ capabilities = \["([^"]+)"\] \}',policy)
        assert clauses==[('iot/data/ci/airflow' if name=='ci' else 'iot/data/gateway/basic','read'),('auth/token/revoke-self','update')]
        role=read('final-readback/role-'+name+'.json')['data'];assert role['token_policies']==['iot-'+name] and role['token_no_default_policy'] and role['bind_secret_id'] and role['token_ttl']==300 and role['token_max_ttl']==900 and role['secret_id_num_uses']==20
    first=read('sync-v1-readback/verification.json');rotated=read('sync-v2/verification.json');rotation=read('rotation/rotation.json');assert first['passed'] and first['read_only_resume'] and not first['password_changed'] and first['secret_version']==1
    assert rotated['passed'] and rotated['secret_version']==2 and rotated['password_changed'] and rotated['new_password_http']==200 and rotated['old_password_http']==401 and rotated['cross_ci_read_http']==403
    assert first['secret_uids']==rotated['secret_uids'] and len(rotated['secret_uids'])==3
    assert rotation['before_version']==1 and rotation['after_version']==2 and rotation['stale_cas_http']==400
    routes=read('final-readback/gateway-four-routes.json');assert routes['passed'] and routes['secret_version']==2 and len(routes['routes'])==4 and all(r['statuses']=={'old':401,'new':200} and r['tls']=='TLSv1.3' and r['ca_and_hostname_required'] for r in routes['routes'])
    before=read('restart/before.json');sealed=read('restart/sealed.json');after=read('restart/after.json');restart=read('restart/verification.json')
    assert before['http']==after['http']==200 and sealed['http']==503 and sealed['health']['initialized'] and sealed['health']['sealed']
    assert before['health']['cluster_id']==after['health']['cluster_id']==restart['same_cluster_id'] and after['health']['version']=='2.2.1' and not after['health']['sealed'] and not after['health']['standby']
    assert restart['passed'] and restart['exact_private_values_preserved'] and [(x['role'],x['version'],x['exact_private_value_match']) for x in read('restart/consumers-after.json')]==[('ci',1,True),('gateway',2,True)]
    records=[json.loads(line) for line in (BASE/'restart/scoped-audit.jsonl').read_bytes().splitlines()];groups=defaultdict(list)
    for record in records:
        assert record['auth']['display_name']=='approle' and record['auth']['policies'] in [['iot-ci'],['iot-gateway']] and record['auth']['token_ttl']==300
        assert record['auth']['client_token'].startswith('hmac-sha256:') and record['request']['client_token'].startswith('hmac-sha256:')
        groups[record['request']['id']].append(record)
    assert len(records)==22 and len(groups)==11 and all({v['type'] for v in pair}=={'request','response'} and len(pair)==2 for pair in groups.values())
    denied=0;digests=defaultdict(set)
    for pair in groups.values():
        response=next(v for v in pair if v['type']=='response');role=response['auth']['policies'][0];path=response['request']['path'];allowed=(role=='iot-ci' and path=='iot/data/ci/airflow') or (role=='iot-gateway' and path=='iot/data/gateway/basic')
        if not allowed:assert 'permission denied' in response['error'];denied+=1;continue
        data=response['response']['data'];version=data['metadata']['version'];assert all(isinstance(v,str) and v.startswith('hmac-sha256:') for v in data['data'].values());digests[(path,version)].add(data['data']['password'])
    assert denied==4 and set(digests)=={('iot/data/ci/airflow',1),('iot/data/gateway/basic',1),('iot/data/gateway/basic',2)} and all(len(v)==1 for v in digests.values())
    assert digests[('iot/data/gateway/basic',1)]!=digests[('iot/data/gateway/basic',2)]
    unit=dict(line.split('=',1) for line in (BASE/'restart/systemd.txt').read_text().splitlines());assert unit['ActiveState']=='active' and unit['User']=='nobody' and unit['NoNewPrivileges']=='yes' and int(unit['MemoryMax'])==768*1024**2 and unit['MemorySwapMax']=='0'
    config=(BASE/'resume-active/vault.hcl').read_text();assert 'storage "raft"' in config and 'tls_min_version = "tls12"' in config and 'address = "10.52.0.1:18200"' in config and 'disable_mlock = true' in config
    assert ' -dev' not in (BASE/'resume-active/vault.service').read_text()
    cert=ssl._ssl._test_decode_cert(str(BASE/'resume-active/server.crt'));assert set(cert['subjectAltName'])=={('DNS','vault.iot.test'),('IP Address','10.52.0.1')}
    assert (BASE/'resume-active/ca.crt').read_bytes()==(ROOT/'docs/evidence/private-gateway-linux/deploy/ca.crt').read_bytes()
    pins=json.loads((ROOT/'infra/security/vault-assets.json').read_bytes());asset=read('assets/download.json');assert asset['archive_sha256']==pins['archive_sha256'] and asset['binary_sha256']==pins['binary_sha256'] and asset['version'].startswith('Vault v2.2.1')
    assert read('privacy-audit.json')['passed'] and read('privacy-audit.json')['private_encodings_checked']==14 and all(int(v['mode'],8)&0o077==0 for v in read('private-permissions.json').values())
    assert 'no such file or directory' in (BASE/'initial-failure/journal.txt').read_text() and read('initial-failure/repair.json')['bootstrap_absent']
    assert 'AssertionError' in (BASE/'ci-1/console.txt').read_text() and read('capture-finish.json')['prior_bytes_unchanged']
    for name in ['jenkins-vault-success.jpg','vault-active-health.jpg']:assert (BASE/'screenshots'/name).read_bytes().startswith(b'\xff\xd8\xff')
    print('PASS81payload/2originalJPEG/41historicalGitbindings/current10inputs;Jenkins2SUCCESS2977ms/4stages/3nativecontracts;AppRole300s/actualAirflow201+200/cross403/revoked403;KV2CAS400/rotationold401new200on4hosts;realRaftrestart503sealed-to-unseal200/samecluster/versions1+2;22HMACauditrecords/11requests/4denied/stable-versionHMACs;14privateencodings excluded;initial failures retained. Single VM/operator demo, not HA or complete organization migration.')


if __name__=='__main__':main()
