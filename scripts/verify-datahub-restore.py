"""Verify same-image/same-mount DataHub restoration and indexed historical lineage/assertions without credentials."""
from evidence_integrity import ROOT,verified
BASE=ROOT/'docs/evidence/datahub-restore-linux'


def main():
    """Compare retained runtime identities and original indexed Feast dataset/job/assertion links."""
    read=verified(BASE,9,8);proof=read('restore-v2/verification.json');assert proof['passed'] and proof['gms_http']==proof['frontend_login_http']==200 and proof['upgrade_not_rerun']
    old={v['Name'].split('iot-datahub-')[1].removesuffix('-1'):v for v in read('before.json')};rows=proof['services'];assert {v['service'] for v in rows}=={'mysql','search','broker','datahub-gms','datahub-frontend'}
    for row in rows:
        before=old[row['service']];assert before['Id']==row['before_id'] and row['after_id']!=row['before_id'] and row['exact_mounts_preserved'] and row['config_preserved_except_image_reference_pinned_to_same_id']
        assert 'does not exist' in before['State']['Error'] and row['new_bridge']=='iot-metadata-restored-28b'
        assert {(m['Type'],m.get('Name'),m['Destination'],m['RW']) for m in before['Mounts']}=={(m['type'],m['name'],m['destination'],m['read_write']) for m in row['mounts']}
        for ports in (row['ports'] or {}).values():assert all(p['HostIp']=='127.0.0.1' for p in ports)
    for row in read('config-parity.json'):assert row['before_config_sha256']==row['after_normalized_config_sha256'] and row['status']=='running' and row['after_image_reference']==row['actual_same_image_id'] and row['config_private_values_omitted']
    import json
    parent=json.loads((ROOT/'docs/evidence/feast-materialization/runs/feast-incremental-27-datahub.json').read_bytes())
    data=[read('indexed/'+n+'.json')['data']['dataset'] for n in ['input','output']];assert {v['urn'] for v in data}=={v['urn'] for v in parent['datasets']}
    for item in data:
        old_item=next(v for v in parent['datasets'] if v['urn']==item['urn']);expected={v['entity']['urn'] for v in old_item['lineage']['relationships'] if v['entity']};assert expected<={v['entity']['urn'] for v in item['lineage']['relationships'] if v['entity']}
    assertions=data[1]['assertions']['assertions'];assert len(assertions)==6 and all(any(e['runId']=='feast-incremental-27' and e['result']['type']=='SUCCESS' for e in a['runEvents']['runEvents']) for a in assertions)
    assert read('indexed/verification.json')['passed'] and read('indexed/verification.json')['lineage_preserved']
    print('PASS9payload;5same-image/same-mountservices/privatebridge;oldcontainersretained/noSystemUpdate;GMS200/frontend200;2originalindexedDatasets/Feastjoblineage/6SUCCESSassertionssurvived. Full secret Config omitted; hashes attest native equality after image-reference normalization.')


if __name__=='__main__':main()
