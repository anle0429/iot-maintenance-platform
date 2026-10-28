"""Audit the saved Linux logging snapshot and strict source-clock comparisons using Git and the standard library only."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import json,re,struct,subprocess,sys


def main():
    """Verify historical evidence; this does not start a collector or assert the current VM is running."""
    root=Path(__file__).resolve().parents[1];base=root/'docs/evidence/log-monitoring-linux'
    read=lambda name:json.loads((base/name).read_bytes());digest=lambda raw:sha256(raw).hexdigest()
    manifest=read('manifest.json');assert len(manifest['files'])==58
    for name,item in manifest['files'].items():
        assert re.fullmatch(r'[A-Za-z0-9_.-]+',name)
        raw=(base/name).read_bytes();assert len(raw)==item['bytes']<1_000_000 and digest(raw)==item['sha256'],name
    assert sum(x['bytes'] for x in manifest['files'].values())==manifest['payload_bytes']==751258
    for index in ['native-publication.json','native-capture.json']:
        for name,item in read(index)['files'].items():
            raw=(base/name).read_bytes();assert len(raw)==item['bytes'] and digest(raw)==item['sha256'],name
    old=read('prepared-ci4.json')['source'];current=read('keep-started.json')['source']
    assert len(old['files'])==6 and len(current['files'])==8 and current==read('oracle-source.json')
    for source in [old,current]:
        for name,expected in source['files'].items():
            raw=subprocess.check_output(['git','show',source['commit']+':'+name],cwd=root);assert digest(raw)==expected,name
    # The independent arithmetic being executed must still equal its recorded source.
    raw=(root/'tests/log_monitoring_oracle.py').read_bytes().replace(b'\r\n',b'\n')
    assert digest(raw)==current['files']['tests/log_monitoring_oracle.py']
    sys.path.insert(0,str(root));from tests.log_monitoring_oracle import raw_records,query_records,compare_logs,compare_counts
    mapping=read('source-map.json');records=raw_records(mapping,{m['file']:(base/m['file']).read_bytes() for m in mapping})
    context=read('query-context.json');start,end=[int(context[k]) for k in ['start_ns','end_ns']]
    metric_start,metric_end,step=[int(context[k]) for k in ['metric_start_ns','metric_end_ns','step_ns']]
    assert len(mapping)==5 and len(records)==237 and {r['labels']['service'] for r in records}=={'node-exporter','prometheus','grafana','predictor','feature-api'}
    assert all(r['labels']['collection']=='native-28-keep' for r in records)
    for engine in ['loki','grafana']:
        for suffix,source,number in [('all',None,237),('host','host-file',229),('applications','cri',8)]:
            assert compare_logs(records,read(engine+'-'+suffix+'.json'),start,end,source)==number
        assert compare_logs(records,read(engine+'-after-restart.json'),start,end)==237
        assert compare_counts(records,read(engine+'-panel-1.json'),metric_start,metric_end,step)=={'returned_samples':80,'positive_samples':80,'groups':5}
        assert compare_counts(records,read(engine+'-panel-2.json'),metric_start,metric_end,step,'cri')=={'returned_samples':2,'positive_samples':2,'groups':2}
    before_mapping=read('before-source-map.json')
    prior=raw_records(before_mapping,{m['file']:(base/('before-'+m['file'])).read_bytes() for m in before_mapping})
    prior_context=read('before-query-context.json');lo,hi=[int(prior_context[k]) for k in ['start_ns','end_ns']]
    prior=[r for r in prior if lo<=r['timestamp_ns']<=hi];actual=query_records(read('before-loki-all.json'));assert len(prior)==len(actual)==219
    identity=lambda r:(tuple(sorted(r['labels'].items())),r['line'])
    assert len({identity(r) for r in prior})==219 and {identity(r) for r in prior}=={identity(r) for r in actual}
    actual={identity(r):r['timestamp_ns'] for r in actual};deltas=[actual[identity(r)]-r['timestamp_ns'] for r in prior]
    assert sum(v!=0 for v in deltas)==19 and min(deltas)==0 and all(v>=0 for v in deltas)
    assert read('before-differences.json')['missing_by_service']=={'node-exporter':10,'prometheus':9}
    assert (base/'config.alloy').read_text().count('action_on_duplicate_timestamp = "keep"')==3
    assert 'action_on_duplicate_timestamp' not in (base/'before-config.alloy').read_text()
    template=read('dashboard.json');actual_dashboard=read('grafana-dashboard.json')['dashboard']
    assert len(template['panels'])==4 and [(p['id'],p['targets'][0]['expr']) for p in template['panels']]==[(p['id'],p['targets'][0]['expr']) for p in actual_dashboard['panels']]
    datasource=read('grafana-datasource.json');assert datasource['uid']=='iot-logs-loki' and datasource['url']=='http://127.0.0.1:13100' and not datasource['isDefault']
    assert read('loki-build.json')['version']=='3.7.9' and '1.21.2' in (base/'alloy-version.txt').read_text()
    assert read('downloads.json')[0]['sha256']=='17a5fae28ce733b3560c27c76620920b169bd2aea9473947b733b511f92e81dd'
    assert read('downloads.json')[1]['sha256']=='1eadb46a0640127184250173acd9b4145e4d590680db37ffe66fa8afd07f04cf'
    prior_units,units=read('units-before.json'),read('units-after.json')
    for name,user,limit in [('loki','nobody',1536*1024**2),('alloy','root',768*1024**2)]:
        value=units[name];assert value['ActiveState']=='active' and int(value['MainPID'])>0 and value['User']==user
        assert value['NoNewPrivileges']=='yes' and value['ProtectSystem']=='strict' and value['CapabilityBoundingSet']==''
        assert int(value['MemoryMax'])==limit and 0<int(value['MemoryCurrent'])<=limit
    assert prior_units['alloy']['MainPID']!=units['alloy']['MainPID'] and prior_units['loki']['MainPID']==units['loki']['MainPID']
    listeners=(base/'listeners.txt').read_text().splitlines();assert len(listeners)==3
    for line in listeners:
        match=re.search(r'127\.0\.0\.1:(13100|19096|12345)\b',line);assert match
        assert 'pid='+units['alloy' if match[1]=='12345' else 'loki']['MainPID']+',' in line
    assert 'Ran 10 tests' in (base/'keep-source-unit-tests.txt').read_text()
    for name in ['overview.jpg','details.jpg']:
        raw=(base/name).read_bytes();assert raw[:2]==b'\xff\xd8';pos=2;size=None
        while pos<len(raw):
            assert raw[pos]==255;marker=raw[pos+1];pos+=2;length=int.from_bytes(raw[pos:pos+2],'big')
            if marker in [192,193,194]:size=struct.unpack('>HH',raw[pos+3:pos+7]);break
            pos+=length
        assert size==(720,1280)
    print('PASS58payload/native hashes/6old+8current Git bindings/237source records through both query engines/164metric samples/19prior clock changes retained/collector restart/3loopback listeners/10native unit tests/2original JPEGs. Historical five-file capture; not automatic discovery, retention expiry, traces or API mutation evidence.')


if __name__=='__main__':main()
