"""Audit native scoped discovery, forty exact CRI records, two Pod identities and preserved restart state offline."""
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
from hashlib import sha256
import json
from evidence_integrity import ROOT,verified,source_bindings
BASE=ROOT/'docs/evidence/log-discovery-linux'


def main():
    """Recalculate timestamp/message/identity equality from raw CRI and independent Loki query payloads."""
    read=verified(BASE,126,122);assert sum(source_bindings(read('sources/'+n+'.json')) for n in ['initial','failed-check','seccomp-repair','fixed-check'])==27
    assert read('start/verification.json')['passed'] and read('start/verification.json')['loki_pid_unchanged']
    assert [v['http'] for v in read('start/rbac.json')]==[200,200,403,403,403]
    token=read('start/token-scope.json');assert token['namespaces']==['iot-features','iot-serving'] and token['token_value_captured'] is False and token['expires_utc']=='2026-10-28T23:10:59Z'
    before=(BASE/'repair/before.alloy').read_bytes();after=(BASE/'repair/after.alloy').read_bytes();assert after==before.replace(b'/var/log/pods/$1_$2_$3/$4/*.log',b'/var/log/pods/${1}_${2}_${3}/${4}/*.log')
    repair=read('repair/verification.json');assert repair['before_sha256']==sha256(before).hexdigest() and repair['after_sha256']==sha256(after).hexdigest() and repair['positions_and_probe_untouched']
    assert b'ca_file =' in after and b'bearer_token_file =' in after and b'names = ["iot-features", "iot-serving"]' in after and b'insecure_skip_verify' not in after
    first=read('check-v3/first-pods.json')['items'][0];second=read('check-v3/second-pods.json')['items'][0];uids=[first['metadata']['uid'],second['metadata']['uid']];assert len(set(uids))==2
    assert read('check-v3/resume.json')['first_pod_and_cri_retained'] and (BASE/'check-v2/first.cri').read_bytes()==(BASE/'check-v3/first.cri').read_bytes()==(BASE/'check-v3/prior-first.cri').read_bytes()
    expected=[]
    for batch,uid in zip(['first','second'],uids,strict=True):
        lines=(BASE/('check-v3/'+batch+'.cri')).read_text().splitlines();assert len(lines)==20
        for i,line in enumerate(lines):
            ts,stream,flag,message=line.split(' ',3);assert stream=='stdout' and flag=='F' and json.loads(message)=={'experiment':'iot-log-discovery-28','batch':batch,'sequence':i,'pod_uid':uid}
            whole,fraction=ts.removesuffix('Z').split('.');ns=str(int(datetime.fromisoformat(whole).replace(tzinfo=timezone.utc).timestamp())*10**9+int(fraction.ljust(9,'0')));expected.append((uid,ns,message))
    assert Counter(tuple(v) for v in read('check-v3/expected.json'))==Counter(expected)
    for name in ['final-readback/probe-after-flush.json','hardening/probe.json']:
        payload=read(name);actual=[(s['stream']['pod_uid'],ts,message) for s in payload['data']['result'] for ts,message in s['values']];assert len(actual)==40 and Counter(actual)==Counter(expected)
    a=(BASE/'check-v3/collector-before.stdout').read_text().strip();b=(BASE/'check-v3/collector-after.stdout').read_text().strip();assert a!=b and a!='0' and b!='0'
    proof=read('final-readback/verification.json');assert proof['passed'] and proof['duplicate_rows_after_restart']==0 and proof['loki_pid_unchanged'] and proof['exact_timestamp_message_identity_match']
    assert {s['service'] for s in proof['current_application_services']}=={'feature-api','drift-api','ab-api','predictor'}
    for service in proof['current_application_services']:
        payloads=[json.loads(p.read_bytes()) for p in (BASE/'final-readback').glob('application-'+service['service']+'-*.json')]
        assert any(any(s['stream']['pod_uid']==service['uid'] and s['stream']['service']==service['service'] and s['values'] for s in p['data']['result']) for p in payloads)
    assert read('final-readback/flush.json')['http'] in [200,204] and 'query_ingesters_within: 3h0m0s' in (BASE/'final-readback/query-settings.txt').read_text()
    unit=dict(l.split('=',1) for l in (BASE/'hardening/unit.txt').read_text().splitlines());assert unit['ActiveState']=='active' and unit['ProtectSystem']=='strict' and unit['NoNewPrivileges']=='yes' and unit['CapabilityBoundingSet']=='' and int(unit['MemoryMax'])==768*1024**2 and unit['MemorySwapMax']=='0'
    assert read('hardening/verification.json')['forty_original_lines_still_exact']
    assert 'timed out' in (BASE/'check/first-ready.stderr').read_text() and not read('diagnosis/probe-pods.json')['items']
    assert 'seccompProfile' not in read('check/first-deployment.json')['spec']['template']['spec']['securityContext'] and read('check-v3/first-deployment.json')['spec']['template']['spec']['securityContext']['seccompProfile']=={'type':'RuntimeDefault'}
    for n in ['before-path.jpg','correct-path.jpg','grafana-discovered-logs.jpg']:assert (BASE/'screenshots'/n).read_bytes().startswith(b'\xff\xd8\xff')
    print('PASS126payload/3originalJPEG/27Gitbindings;scopedTLSRBAC200,200,403,403,403;nativeAlloy1.21.2;twoPodUIDs/40exactCRI-Lokirows;restart+hardeningrestart/noaddedduplicates;4currentAPIs;LokiPIDsame/backingstoreflush;failedattemptsretained. No arbitrary-crash exactly-once or rotation guarantee.')


if __name__=='__main__':main()
