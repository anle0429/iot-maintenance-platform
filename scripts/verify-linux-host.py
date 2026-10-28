"""Audit saved genuine Linux host metrics, original native hashes and same-time numeric readbacks; starts no service."""
from pathlib import Path,PurePosixPath
from hashlib import sha256
import json,re,subprocess,sys


def main():
    """Bind source and every raw sample to the snapshot, then verify Prometheus/Grafana against separate arithmetic."""
    root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
    from tests.linux_monitoring_oracle import expected_linux,compare_linux
    from ml.host_monitoring import profile,queries,dashboard,DATASOURCE_UID
    base=root/'docs/evidence/host-monitoring-linux';read=lambda n:json.loads((base/n).read_bytes());digest=lambda b:sha256(b).hexdigest()
    manifest=read('manifest.json');capture=read('capture.json');proof=read('host/verification.json');started=read('monitor-started.json')
    assert len(manifest['files'])==21 and len(capture['files'])==18 and proof['passed']
    for name,item in manifest['files'].items():
        assert not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts
        raw=(base/name).read_bytes();assert len(raw)==item['bytes']<1_000_000 and digest(raw)==item['sha256'],name
    assert sum(x['bytes'] for x in manifest['files'].values())==manifest['payload_bytes']
    for name,item in capture['files'].items():
        raw=(base/name).read_bytes();assert len(raw)==item['bytes'] and digest(raw)==item['sha256'],name
    assert sum(x['bytes'] for x in capture['files'].values())==capture['payload_bytes']
    sources=[started['source'],read('host/oracle-source.json')]
    assert sources[0]['commit']==proof['source_commit']==manifest['runtime_source_commit'] and sources[1]['commit']==proof['oracle_commit']==manifest['oracle_source_commit']
    assert len(sources[0]['files'])==5 and len(sources[1]['files'])==4
    for source in sources:
        for name,h in source['files'].items():
            raw=subprocess.check_output(['git','show',source['commit']+':'+name],cwd=root);current=(root/name).read_bytes()
            assert digest(raw)==h and (current==raw or current.replace(b'\r\n',b'\n')==raw),name
    assert read('runtime/dashboards/host.json')==dashboard('linux','vm-linux','Linux VM actual metrics')
    raw=read('host/raw-samples.json');end=proof['evaluation_time'];expected=expected_linux(raw,end);native=read('host/native-os-sanity.json')
    idle=raw['node_cpu_seconds_total']['data']['result'];assert len(idle)==native['logical_processors']==8 and {x['metric']['cpu'] for x in idle}==set(map(str,range(8)))
    assert all(x['metric']['mode']=='idle' and len(x['values'])>=15 for x in idle)
    assert float(raw['node_memory_MemTotal_bytes']['data']['result'][0]['value'][1])==native['meminfo_bytes']['MemTotal']
    for item in raw['node_filesystem_size_bytes']['data']['result']:assert float(item['value'][1])==native['filesystems'][item['metric']['mountpoint']]['total']
    target={(x['panel'],x['refId']):x for x in queries('linux','vm-linux')};results=read('host/panel-queries.json');assert len(results)==len(target)==proof['queries_per_engine']==11
    document=read('host/dashboard-readback.json')['dashboard'];assert len(document['panels'])==proof['panels']==9
    for panel in document['panels']:
        assert panel['datasource']['uid']==DATASOURCE_UID
        for value in panel['targets']:assert value['expr'].replace('$host','vm-linux')==target[panel['id'],value['refId']]['expr']
    seen=set()
    for item in results:
        key=item['panel'],item['refId'];assert key not in seen and item['expr']==target[key]['expr'] and item['evaluation_time']==end;seen.add(key)
        value=item['prometheus'];assert value['status']=='success' and value['data']['resultType']=='vector'
        compare_linux([(r['metric'],float(r['value'][1])) for r in value['data']['result']],expected[key])
        answer=item['grafana']['results'][key[1]];assert answer.get('status',200)==200 and not answer.get('error');samples=[]
        for frame in answer['frames']:
            for i,field in enumerate(frame['schema']['fields']):
                values=frame['data']['values'][i];assert len(values)==1
                if field['type']=='time':assert values[0]==round(end*1000)
                if field['type']=='number':samples.append((field['labels'],values[0]))
        compare_linux(samples,expected[key])
    assert seen==set(target) and expected[8,'A']==expected[9,'A']==[1]
    scrape=read('runtime/prometheus.json')['scrape_configs'];assert len(scrape)==1 and scrape[0]['job_name']=='iot-host'
    assert scrape[0]['metric_relabel_configs']==[{'source_labels':['__name__'],'regex':'|'.join(profile('linux')['metric_names']),'action':'keep'}]
    allowed=set(profile('linux')['metric_names'])|{'up','scrape_duration_seconds','scrape_samples_scraped','scrape_samples_post_metric_relabeling','scrape_series_added'}
    assert set(read('host/ingested-names.json')['data'])<=allowed
    assert raw['node_exporter_build_info']['data']['result'][0]['metric']['version']==proof['exporter_version']=='1.12.1'
    assert read('host/grafana-health.json')['version']==proof['grafana_version']=='13.3.0' and read('host/grafana-health.json')['database']=='ok'
    assert read('host/prometheus-build.json')['data']['version']==proof['prometheus_version']=='3.13.5'
    limits={'node-exporter':67108864,'prometheus':536870912,'grafana':805306368};units=read('host/units-live.json')
    for name,item in units.items():
        assert item['ActiveState']=='active' and int(item['MainPID'])>0 and item['User']=='nobody' and item['NoNewPrivileges']=='yes'
        assert 0<int(item['MemoryCurrent'])<=int(item['MemoryPeak'])<=int(item['MemoryMax'])==limits[name]
    listeners=read('listeners.json')['listeners'];assert len(listeners)==3 and {x['local_address'] for x in listeners}=={'127.0.0.1:19100','127.0.0.1:19091','127.0.0.1:13001'}
    assert {pid for x in listeners for pid in re.findall(r'pid=(\d+)',x['line'])}=={v['MainPID'] for v in units.values()}
    for name in ['host-overview.jpg','host-network.jpg']:assert (base/name).read_bytes()[:2]==b'\xff\xd8'
    pins={'prometheus':'0388822ace519c5a3a8ef556a541ce1b536e21d872f82deded465bd27a5a0699','node_exporter':'b51d8a76aa2a9156a55d501aca6276fae09e262259a5e4e831d2c2222f084e63','grafana':'cec8f98cc59bc1a1945833b652371a073be678c5c159a9b1695ad63c04fae5a7'}
    assert {v['name']:v['sha256'] for v in read('downloads.json')}==pins
    print('PASS21payload/18native hashes/5runtime+4oracle source bindings/9panels/11queries per engine/raw same-time Linux arithmetic/8CPU+memory+filesystem totals/3live bounded loopback units/2original JPEGs. Historical host-wide VM snapshot, not AB or benchmark evidence.')


if __name__=='__main__':main()
