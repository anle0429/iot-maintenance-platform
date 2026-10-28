"""Verify frozen Linux boundary/partition and HTTP/SQLite property evidence without rerunning services."""
import json,xml.etree.ElementTree as ET
from hashlib import sha256
from evidence_integrity import ROOT,verified,source_bindings
BASE=ROOT/'docs/evidence/api-validation-linux'


def main():
    """Cross-check original JUnit, generated-example totals, boundaries, immutable source and container limits."""
    read=verified(BASE,16,11);seal=read('source.json');assert source_bindings(seal)==31 and seal==read('source-after.json')
    assert seal['commit']=='ff832374349e0825b6f6bbf3f36fd67031533491'
    native=read('native.json');proof=read('result/check/verification.json');assert native['passed'] and proof['passed']
    junit=ET.parse(BASE/'result/check/junit.xml');cases=junit.findall('.//testcase');assert len(cases)==71 and not junit.findall('.//failure') and not junit.findall('.//error') and not junit.findall('.//skipped')
    stats=proof['hypothesis_statistics'];assert sorted(v['passing'] for v in stats.values())==[35,35,50,100] and all(v['failing']==v['invalid']==0 for v in stats.values())
    assert proof['hypothesis_passing_examples']==native['hypothesis_passing_examples']==220 and proof['hypothesis_seed']==20261028
    profile=read('profile.json');assert sha256((BASE/'profile.json').read_bytes()).hexdigest()==proof['profile_sha256'] and len(profile['selectors'])==14
    names={c.attrib['name'] for c in cases}
    for name in ['test_feature_age_boundaries[0-True]','test_feature_age_boundaries[600-True]','test_feature_age_boundaries[601-False]','test_feature_age_boundaries[-1-False]','test_wall_lease_boundaries[3599-True]','test_wall_lease_boundaries[3600-False]','test_batch_boundaries[0-422]','test_batch_boundaries[128-200]','test_batch_boundaries[129-422]','test_http_sqlite_idempotency_under_generated_reordering_and_duplicates']:assert name in names,name
    console=(BASE/'result/check/console.stdout').read_bytes()
    for name in ['boundary-excerpt.txt','hypothesis-excerpt.txt']:assert (BASE/name).read_bytes() in console
    assert b'71 passed' in console and sum(v['passing'] for v in stats.values())==220
    container=read('container.json');host=container['HostConfig'];assert container['State']['ExitCode']==0 and not container['State']['OOMKilled'] and container['Config']['User']=='10001:10001'
    assert host['Memory']==host['MemorySwap']==1024**3 and host['NanoCpus']==2*10**9 and host['NetworkMode']=='none' and host['ReadonlyRootfs'] and host['CapDrop']==['ALL']
    print('PASS16payload/31Gitbindings;nativeLinux71JUnitcases/220generatedpassingexamples;boundaryandpartitionoracles;HTTP/SQLiteidempotency;1GiB2CPU/networknone. Fixturemodel1;notnewcoverageormutationrun.')


if __name__=='__main__':main()
